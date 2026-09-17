from app.models.audit import AuditReport, RiskItem
from app.models.state import HumanReviewItem, finish_node, record_tool_call, start_node
from app.parsers.loader import parse_document
from app.extraction.field_extractor import extract_fields
from app.rag.retriever import retrieve_policy
from app.tools.registry import get_tool


def parse_documents(state: dict) -> dict:
    started_at = start_node(state, "parse_documents", "开始解析上传材料")
    documents = [parse_document(name, content) for name, content in state["files"]]
    finish_node(state, "parse_documents", started_at, f"解析{len(documents)}份材料")
    return {"documents": documents, "trace": state["trace"]}


def extract(state: dict) -> dict:
    started_at = start_node(state, "extract", "开始抽取统一字段")
    fields = extract_fields(state["documents"])
    finish_node(state, "extract", started_at, f"抽取{len(fields)}个字段")
    return {"fields": fields, "trace": state["trace"]}


def retrieve(state: dict) -> dict:
    started_at = start_node(state, "retrieve", "开始检索制度证据")
    city = state["fields"].get("travel_city")
    evidence = retrieve_policy(str(city.value) if city else None)
    record_tool_call(
        state,
        "retrieve_policy",
        "success" if evidence else "skipped",
        f"检索到{len(evidence)}条制度",
        [item.chunk_id for item in evidence],
    )
    finish_node(state, "retrieve", started_at, f"检索到{len(evidence)}条制度")
    return {"policy_evidence": evidence, "trace": state["trace"]}


def check(state: dict) -> dict:
    started_at = start_node(state, "check", "开始执行确定性业务工具")
    fields = state["fields"]
    get = lambda key: str(fields[key].value) if key in fields else None
    tool_inputs = [
        ("required_documents", {"document_names": [d.file_name for d in state["documents"]]}),
        ("amount_match", {"invoice_amount": get("invoice_amount"), "payment_amount": get("payment_amount")}),
        (
            "date_range",
            {
                "invoice_date": get("invoice_date"),
                "travel_start_date": get("travel_start_date"),
                "travel_end_date": get("travel_end_date"),
            },
        ),
        ("hotel_limit", {"invoice_amount": get("invoice_amount"), "policy_evidence": state["policy_evidence"]}),
    ]
    executions = [get_tool(name).execute(**kwargs) for name, kwargs in tool_inputs]
    results = [execution.result for execution in executions]
    for execution in executions:
        result = execution.result
        record_tool_call(
            state,
            execution.spec.name,
            "success" if result.passed is True else "failed" if result.passed is False else "skipped",
            result.detail,
            result.evidence_refs,
            execution.input_summary,
        )
    risks = []
    human_review_items = list(state.get("human_review_items", []))
    for result in results:
        if result.passed is not True:
            risks.append(RiskItem(risk_type=result.name.upper(), level="MEDIUM", reason=result.detail,
                evidence_refs=result.evidence_refs,
                policy_refs=[e.chunk_id for e in state["policy_evidence"]] if result.name == "hotel_limit" else []))
            human_review_items.append(HumanReviewItem(reason=result.detail, evidence_refs=result.evidence_refs))
    finish_node(state, "check", started_at, "执行4项确定性检查")
    return {"checks": results, "risks": risks, "human_review_items": human_review_items, "trace": state["trace"]}


def report(state: dict) -> dict:
    started_at = start_node(state, "report", "开始生成审核报告")
    value = AuditReport(status="REVIEW_REQUIRED" if state["risks"] else "PASS",
        fields=state["fields"], policy_evidence=state["policy_evidence"],
        checks=state["checks"], risks=state["risks"], trace=state["trace"] + [
            f"task_id={state['task_id']}",
            f"steps={state['control'].step_count}/{state['control'].max_steps}",
            f"tool_calls={len(state['tool_calls'])}",
            f"human_review_items={len(state['human_review_items'])}",
        ])
    finish_node(state, "report", started_at, "生成审核报告")
    return {"report": value, "trace": value.trace}
