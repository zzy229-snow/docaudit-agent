from app.agent.risk_rules import build_risk, level_action, overall_status, requires_review, risk_summary
from app.models.audit import AuditReport, RiskItem
from app.models.field import ExtractedField
from app.models.state import HumanReviewItem, finish_node, record_tool_call, start_node
from app.parsers.loader import parse_document
from app.extraction.field_extractor import extract_fields_with_diagnostics
from app.rag.retriever import retrieve_policy
from app.services.injection_guard import detect_injection
from app.tools.registry import get_tool


def parse_documents(state: dict) -> dict:
    started_at = start_node(state, "parse_documents", "开始解析上传材料")
    documents = [parse_document(name, content) for name, content in state["files"]]
    # 任务④:收集 OCR 困难区域(手写/低质),供后续标记人工复核
    hard_regions = []
    for doc in documents:
        for suffix, level in (("_handwriting", "handwriting"), ("_hard", "hard")):
            if doc.document_type.endswith(suffix):
                hard_regions.append({
                    "file_name": doc.file_name,
                    "document_id": doc.document_id,
                    "level": level,
                })
    if hard_regions:
        state.setdefault("trace", []).append(
            f"困难区域检测：{len(hard_regions)}份材料识别质量不足，需人工核对")
    # PRD §15:文档内容视为不可信数据,命中注入话术只记录不改写结论
    injection_hits = []
    for doc in documents:
        snippets = detect_injection(doc.text)
        if snippets:
            injection_hits.append({"file_name": doc.file_name, "snippets": snippets})
    if injection_hits:
        state.setdefault("trace", []).append(
            f"指令注入检测：{len(injection_hits)}份材料含疑似注入文本，已按不可信数据处理")
    finish_node(state, "parse_documents", started_at, f"解析{len(documents)}份材料")
    return {
        "documents": documents,
        "hard_regions": hard_regions,
        "injection_hits": injection_hits,
        "trace": state["trace"],
    }


def extract(state: dict) -> dict:
    started_at = start_node(state, "extract", "开始抽取统一字段")
    outcome = extract_fields_with_diagnostics(state["documents"])
    fields = outcome.fields
    if outcome.llm_diagnostics:
        diag = outcome.llm_diagnostics
        state.setdefault("trace", []).append(
            "llm_extraction: "
            f"attempts={diag.attempts}, "
            f"accepted={len(diag.accepted_fields)}, "
            f"rejected={len(diag.rejected_fields)}, "
            f"fallback_used={diag.fallback_used}"
        )
    for name, override in state.get("field_overrides", {}).items():
        reason = override.get("reason", "人工修正")
        value = override["value"]
        if name in fields:
            fields[name] = fields[name].model_copy(update={
                "value": value,
                "confidence": 1.0,
                "source_text": f"人工修正：{reason}",
            })
        else:
            fields[name] = ExtractedField(
                name=name,
                value=value,
                confidence=1.0,
                document_id="manual",
                page_no=1,
                source_text=f"人工补充：{reason}",
            )
        state.setdefault("trace", []).append(f"字段{name}已人工修正为{value}，原因：{reason}")
    finish_node(state, "extract", started_at, f"抽取{len(fields)}个字段")
    return {"fields": fields, "trace": state["trace"]}


def retrieve(state: dict) -> dict:
    started_at = start_node(state, "retrieve", "开始检索制度证据")
    city = state["fields"].get("travel_city")
    evidence = retrieve_policy(str(city.value) if city else None,
                               files=state.get("files"))
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
        (
            "applicant_match",
            {
                "applicant_name": get("applicant_name"),
                "invoice_buyer": get("invoice_buyer"),
                "payment_party": get("payment_party"),
            },
        ),
        # FR-204:重复发票查询(号码+开票日期+金额),命中返回关联任务编号
        (
            "check_duplicate_invoice",
            {
                "invoice_number": get("invoice_number"),
                "invoice_date": get("invoice_date"),
                "invoice_amount": get("invoice_amount"),
                "task_id": state.get("task_id"),
            },
        ),
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
            risk = build_risk(
                risk_type=result.name,
                reason=result.detail,
                evidence_refs=result.evidence_refs,
                policy_refs=[e.chunk_id for e in state["policy_evidence"]] if result.name == "hotel_limit" else [],
            )
            risks.append(risk)
            # 附录A:仅 HIGH/MEDIUM 创建复核项;LOW/INFO 只提示
            if requires_review(risk.level):
                human_review_items.append(_review_item_for(risk))
    # 任务④:困难区域(手写/低质材料)追加风险与人工复核项
    extracted_documents = {field.document_id for field in state["fields"].values()}
    for region in state.get("hard_regions") or []:
        level = region.get("level", "hard")
        # 手写金额/日期必须人工确认(PRD §6.4);低质材料若仍产出字段则降级为仅展示(附录A INFO)
        if level == "handwriting":
            risk_type = "HANDWRITING_REVIEW"
            severity = None
        elif region.get("document_id") in extracted_documents:
            risk_type = "OCR_QUALITY_REVIEW"
            severity = "INFO"
        else:
            risk_type = "OCR_QUALITY_REVIEW"
            severity = None
        reason = f"{region['file_name']} 含困难区域({level})，OCR 识别质量不足，建议人工核对原文"
        risk = build_risk(
            risk_type=risk_type,
            reason=reason,
            evidence_refs=[region["file_name"]],
            level=severity,
        )
        risks.append(risk)
        if requires_review(risk.level):
            human_review_items.append(_review_item_for(risk))
    # PRD §15:材料中的指令注入文本(HIGH),不改写任何确定性检查结论
    for hit in state.get("injection_hits") or []:
        snippets = "、".join(hit["snippets"])
        risk = build_risk(
            risk_type="PROMPT_INJECTION",
            reason=f"{hit['file_name']} 含疑似指令注入文本（{snippets}），已按不可信数据处理，需人工确认",
            evidence_refs=[hit["file_name"]],
        )
        risks.append(risk)
        if requires_review(risk.level):
            human_review_items.append(_review_item_for(risk))
    finish_node(state, "check", started_at, f"执行6项确定性检查,风险{len(risks)}项")
    return {"checks": results, "risks": risks, "human_review_items": human_review_items, "trace": state["trace"]}


def _review_item_for(risk: RiskItem) -> HumanReviewItem:
    return HumanReviewItem(
        reason=f"[{risk.level}·{level_action(risk.level)}] {risk.reason}",
        evidence_refs=risk.evidence_refs,
    )


def report(state: dict) -> dict:
    started_at = start_node(state, "report", "开始生成审核报告")
    summary = risk_summary(state["risks"])
    value = AuditReport(status=overall_status(state["risks"]),
        fields=state["fields"], policy_evidence=state["policy_evidence"],
        checks=state["checks"], risks=state["risks"], risk_summary=summary, trace=state["trace"] + [
            f"task_id={state['task_id']}",
            f"steps={state['control'].step_count}/{state['control'].max_steps}",
            f"tool_calls={len(state['tool_calls'])}",
            f"human_review_items={len(state['human_review_items'])}",
            f"risk_levels={','.join(f'{k}:{v}' for k, v in summary.items()) or 'none'}",
        ])
    finish_node(state, "report", started_at, "生成审核报告")
    return {"report": value, "trace": value.trace}
