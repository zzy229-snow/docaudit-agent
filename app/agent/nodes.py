from app.agent.planner import build_plan, validate_plan
from app.agent.risk_rules import build_risk, level_action, overall_status, requires_review, risk_summary
from app.models.audit import AuditReport, CritiqueResult, RiskItem
from app.models.field import ExtractedField
from app.models.state import (
    HumanReviewItem,
    budget_exhausted,
    finish_node,
    no_progress_detected,
    record_tool_call,
    record_tool_retry,
    start_node,
)
from app.parsers.loader import parse_document
from app.extraction.field_extractor import extract_fields_with_diagnostics
from app.rag import retriever as rag_retriever
from app.rag.retriever import retrieve_policy
from app.services.injection_guard import detect_injection
from app.services.material_type import MATERIAL_LABELS, classify_documents, material_check_inputs
from app.services.model_gateway import ModelGateway
from app.services.readability import assess_readability, human_provided_fields
from app.tools.registry import get_tool

import os
import time

#: 字段缺失导致"无法核对"时不再沿用"不一致"的等级(金额缺失 ≠ 金额不符)
UNVERIFIABLE_RISK_TYPES: dict[str, str] = {"amount_match": "AMOUNT_UNVERIFIABLE"}


def _risk_type_for(result) -> str:
    """检查项 → 风险类型:passed 为 None(缺字段无结论)时用 *_UNVERIFIABLE 类型。"""
    if result.passed is None:
        return UNVERIFIABLE_RISK_TYPES.get(result.name, result.name)
    return result.name


def _retry_backoff_seconds() -> float:
    """工具重试退避基数(测试可设为 0)。"""
    try:
        return max(0.0, float(os.environ.get("TOOL_RETRY_BACKOFF_SECONDS", "0.05")))
    except ValueError:
        return 0.05


def parse_documents(state: dict) -> dict:
    started_at = start_node(state, "parse_documents", "开始解析上传材料")
    # 发票专用 OCR 接口返回的结构化字段在解析时收集(见 loader.parse_document 的 field_sink)
    ocr_fields: dict[str, str] = {}
    documents = [parse_document(name, content, field_sink=ocr_fields)
                 for name, content in state["files"]]
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
        "ocr_fields": ocr_fields,
        "trace": state["trace"],
    }


def extract(state: dict) -> dict:
    started_at = start_node(state, "extract", "开始抽取统一字段")
    # 运行时模型配置(界面"填空"或 API 传入)优先于环境变量
    gateway = ModelGateway(**(state.get("llm_settings") or {}))
    outcome = extract_fields_with_diagnostics(state["documents"], gateway=gateway)
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
    # 发票专用 OCR 接口的结构化字段:精度高于版面正则,优先采用(人工修正仍然最高)
    api_fields = state.get("ocr_fields") or {}
    if api_fields:
        conflicts: list[str] = []
        for name, value in api_fields.items():
            current = fields.get(name)
            if current is not None and str(current.value) != str(value):
                conflicts.append(f"{name}(接口={value} 规则={current.value})")
            fields[name] = ExtractedField(
                name=name,
                value=value,
                confidence=0.99,
                document_id="ocr-api",
                page_no=1,
                source_text=f"OCR结构化字段：{name}={value}",
            )
        state.setdefault("trace", []).append(
            f"ocr_structured_fields: 采用{len(api_fields)}项接口结构化字段（优先于规则抽取）")
        if conflicts:
            state.setdefault("trace", []).append(
                f"ocr_structured_fields_conflict: 接口值与规则抽取不一致,已采用接口值 —— {';'.join(conflicts)}")
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
    context = state.get("user_context") or {}
    evidence = retrieve_policy(str(city.value) if city else None,
                               files=state.get("files"),
                               department=context.get("department"),
                               as_of=context.get("as_of"))
    # 检索引擎与是否降级必须留在 trace 里:降级到 mock 时结论看起来一样,但依据来源不同
    note = rag_retriever.RETRIEVAL_NOTE
    record_tool_call(
        state,
        "retrieve_policy",
        "success" if evidence else "skipped",
        f"检索到{len(evidence)}条制度",
        [item.chunk_id for item in evidence],
    )
    finish_node(state, "retrieve", started_at, f"检索到{len(evidence)}条制度")
    return {"policy_evidence": evidence, "trace": [*state["trace"], f"rag: {note}"]}


def plan(state: dict) -> dict:
    """§4.1 阶段4/§7.2 plan:按报销类型、材料与字段生成受控审核计划。"""
    started_at = start_node(state, "plan", "生成受控审核计划")
    audit_plan = build_plan(state["task_id"], fields=state.get("fields"),
                            policy_evidence=state.get("policy_evidence"))
    issues = validate_plan(audit_plan)
    if issues:
        audit_plan = audit_plan.model_copy(
            update={"status": "needs_replan", "notes": [*audit_plan.notes, *issues]}
        )
    skipped = [step.tool for step in audit_plan.steps if step.may_skip]
    detail = f"生成{len(audit_plan.steps)}步审核计划"
    if skipped:
        detail += f"，其中{len(skipped)}步字段不足可能无结论"
    finish_node(state, "plan", started_at, detail)
    return {"audit_plan": audit_plan, "trace": state["trace"]}


def _execute_with_retry(state: dict, tool_name: str, kwargs: dict):
    """执行工具,失败按指数退避重试(PRD §7.4 单工具重试次数,幂等工具才重试)。"""
    tool = get_tool(tool_name)
    control = state["control"]
    if not tool.spec.idempotent:
        return tool.execute(**kwargs)
    for attempt in range(control.max_tool_retries + 1):
        try:
            return tool.execute(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 重试耗尽后向上抛出,由流程终止
            if attempt >= control.max_tool_retries:
                raise
            record_tool_retry(state, tool_name, attempt + 1, f"{type(exc).__name__}: {exc}")
            time.sleep(_retry_backoff_seconds() * (2 ** attempt))
    raise RuntimeError(f"工具 {tool_name} 重试失败")


def check(state: dict) -> dict:
    started_at = start_node(state, "check", "开始执行确定性业务工具")
    fields = state["fields"]
    get = lambda key: str(fields[key].value) if key in fields else None
    # 材料类型按票面内容判定,文件名只在内容判不出时兜底(客户文件名常是"微信图片_xxx")
    fallback_names, content_types = material_check_inputs(state["documents"], state.get("ocr_fields"))
    detected = classify_documents(state["documents"], state.get("ocr_fields"))
    if detected:
        state.setdefault("trace", []).append(
            "material_type: " + "、".join(f"{name}→{MATERIAL_LABELS.get(kind, '不明')}"
                                          for name, kind in detected.items()))
    tool_inputs = [
        ("required_documents", {"document_names": fallback_names, "material_types": content_types}),
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
    executions = [_execute_with_retry(state, name, kwargs) for name, kwargs in tool_inputs]
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
                risk_type=_risk_type_for(result),
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


def critic(state: dict) -> dict:
    """§7.2 critic:复核完整性、证据覆盖率与制度引用是否齐全。"""
    started_at = start_node(state, "critic", "自检完整性/证据/引用")
    checks = state.get("checks") or []
    risks = state.get("risks") or []
    with_conclusion = [check for check in checks if check.passed is not None]
    with_evidence = [risk for risk in risks if risk.evidence_refs]
    coverage = round(len(with_evidence) / len(risks), 4) if risks else 1.0

    issues: list[str] = []
    skipped = [check.name for check in checks if check.passed is None]
    if skipped:
        issues.append(f"以下检查项因字段不足无结论:{'、'.join(skipped)}")
    missing_evidence = [risk.risk_type for risk in risks if not risk.evidence_refs]
    if missing_evidence:
        issues.append(f"以下风险缺少材料/规则证据:{'、'.join(missing_evidence)}")
    missing_policy = [risk.risk_type for risk in risks
                      if risk.risk_type == "HOTEL_LIMIT" and not risk.policy_refs]
    if missing_policy:
        issues.append(f"以下风险缺少制度条款引用:{'、'.join(missing_policy)}")

    result = CritiqueResult(
        complete=not issues,
        issues=issues,
        checks_total=len(checks),
        checks_with_conclusion=len(with_conclusion),
        risks_total=len(risks),
        risks_with_evidence=len(with_evidence),
        evidence_coverage=coverage,
        missing_policy_risks=missing_policy,
    )
    finish_node(
        state, "critic", started_at,
        "自检通过" if result.complete else f"自检发现{len(issues)}项问题",
    )
    return {"critique": result, "trace": state["trace"]}


def route_review(state: dict) -> dict:
    """§7.2 route_review:决定继续 / 转人工 / 终止(§7.4 防循环与预算门控)。"""
    started_at = start_node(state, "route_review", "决定继续/人工/失败")
    control = state["control"]
    risks = state.get("risks") or []
    blocking = [risk for risk in risks if requires_review(risk.level)]
    critique = state.get("critique")
    budget_reason = budget_exhausted(state)

    if control.terminated_reason:
        route, reason = "fail", control.terminated_reason
    elif no_progress_detected(state):
        route, reason = "fail", "相同工具与相同参数连续重复，判定无进展（PRD §7.4）"
    elif budget_reason:
        route, reason = "fail", budget_reason
    elif blocking:
        levels = "、".join(sorted({risk.level for risk in blocking}))
        route, reason = "review", f"存在{len(blocking)}项{levels}风险，需人工复核"
    elif critique and critique.issues:
        route, reason = "review", f"自检发现问题:{critique.issues[0]}"
    else:
        route, reason = "continue", "未发现阻断性风险，自动完成"

    control.route = route
    control.route_reason = reason
    finish_node(state, "route_review", started_at, f"路由={route}（{reason}）")
    return {"control": control, "trace": state["trace"]}


def report(state: dict) -> dict:
    started_at = start_node(state, "report", "开始生成审核报告")
    control = state["control"]
    risks = list(state.get("risks") or [])
    # 材料可读性:读不出来就不给结论(默认演示模式下图片/扫描件从未被识别)
    verdict = assess_readability(state.get("documents") or [], state.get("fields") or {})
    state.setdefault("trace", []).append(verdict.as_trace())
    unreadable = not verdict.readable
    human_review_items = list(state.get("human_review_items") or [])
    fields = dict(state.get("fields") or {})
    if unreadable:
        # 仅保留与"是否识别"无关的材料齐全性判断,其余建立在演示文本上的风险全部作废
        keep = [risk for risk in risks if risk.risk_type == "REQUIRED_DOCUMENTS"]
        blocker = build_risk(
            risk_type="MATERIAL_UNREADABLE",
            reason=verdict.reason or "材料未被识别，无法作出审核结论。",
            evidence_refs=verdict.unreadable_files,
        )
        risks = [blocker, *keep]
        human_review_items = [_review_item_for(risk) for risk in risks if requires_review(risk.level)]
        state["human_review_items"] = human_review_items
        # 未识别文本里抽出来的字段不是证据,不展示(只留人工填写/修正的字段)
        fields = human_provided_fields(fields)
        state.setdefault("trace", []).append(
            f"material_unreadable: {verdict.code} —— 已作废{len(state.get('risks') or [])}项基于未识别文本的风险")
    summary = risk_summary(risks)
    if control.route == "fail":
        status, failure_reason = "FAILED", control.route_reason
    elif unreadable:
        status, failure_reason = "UNDETERMINED", verdict.reason
    else:
        status, failure_reason = overall_status(risks), None
    value = AuditReport(status=status,
        fields=fields, policy_evidence=state["policy_evidence"],
        checks=state["checks"], risks=risks, risk_summary=summary,
        audit_plan=state.get("audit_plan"), critique=state.get("critique"),
        failure_reason=failure_reason, trace=state["trace"] + [
            f"task_id={state['task_id']}",
            f"steps={state['control'].step_count}/{state['control'].max_steps}",
            f"tool_calls={len(state['tool_calls'])}",
            f"human_review_items={len(human_review_items)}",
            f"risk_levels={','.join(f'{k}:{v}' for k, v in summary.items()) or 'none'}",
            f"route={control.route}",
        ])
    finish_node(state, "report", started_at, "生成审核报告")
    return {"report": value, "trace": value.trace, "human_review_items": human_review_items}
