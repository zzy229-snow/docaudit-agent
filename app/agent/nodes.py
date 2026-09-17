from app.models.audit import AuditReport, RiskItem
from app.parsers.loader import parse_document
from app.extraction.field_extractor import extract_fields
from app.rag.retriever import retrieve_policy
from app.tools.amount_tool import compare_amounts, compare_hotel_limit
from app.tools.date_tool import compare_date_range
from app.tools.document_tool import check_required_documents


def parse_documents(state: dict) -> dict:
    documents = [parse_document(name, content) for name, content in state["files"]]
    return {"documents": documents, "trace": state.get("trace", []) + [f"解析{len(documents)}份材料"]}


def extract(state: dict) -> dict:
    fields = extract_fields(state["documents"])
    return {"fields": fields, "trace": state["trace"] + [f"抽取{len(fields)}个字段"]}


def retrieve(state: dict) -> dict:
    city = state["fields"].get("travel_city")
    evidence = retrieve_policy(str(city.value) if city else None)
    return {"policy_evidence": evidence, "trace": state["trace"] + [f"检索到{len(evidence)}条制度"]}


def check(state: dict) -> dict:
    fields = state["fields"]
    get = lambda key: str(fields[key].value) if key in fields else None
    results = [
        check_required_documents([d.file_name for d in state["documents"]]),
        compare_amounts(get("invoice_amount"), get("payment_amount")),
        compare_date_range(get("invoice_date"), get("travel_start_date"), get("travel_end_date")),
        compare_hotel_limit(get("invoice_amount"), state["policy_evidence"]),
    ]
    risks = []
    for result in results:
        if result.passed is not True:
            risks.append(RiskItem(risk_type=result.name.upper(), level="MEDIUM", reason=result.detail,
                evidence_refs=result.evidence_refs,
                policy_refs=[e.chunk_id for e in state["policy_evidence"]] if result.name == "hotel_limit" else []))
    return {"checks": results, "risks": risks, "trace": state["trace"] + ["执行4项确定性检查"]}


def report(state: dict) -> dict:
    value = AuditReport(status="REVIEW_REQUIRED" if state["risks"] else "PASS",
        fields=state["fields"], policy_evidence=state["policy_evidence"],
        checks=state["checks"], risks=state["risks"], trace=state["trace"] + ["生成审核报告"])
    return {"report": value, "trace": value.trace}
