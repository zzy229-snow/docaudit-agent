"""受控审核计划生成(PRD §4.1 阶段4 / §7.2 plan 节点 / §7.1 设计原则)。

原则:
- 计划只由**规则**或受约束的 LLM 生成,步骤必须落在工具白名单内(``TOOL_REGISTRY``);
- 计划根据报销类型、材料和已抽取字段决定哪些检查项可能被跳过(例如缺"出差城市"
  时住宿标准检查无制度依据),但不改变确定性检查的执行集合;
- 计划可版本化(``AuditPlan.version``),便于评测与复盘对比。

本模块不调用模型,保证可复现;接入 LLM Planner 时应复用同一 Schema 与白名单校验。
"""
from __future__ import annotations

from app.models.audit import AuditPlan, PlanStep
from app.tools.registry import TOOL_REGISTRY

#: 检查项 -> 依赖的关键字段(缺失则该步无结论/跳过)
STEP_FIELD_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "required_documents": (),
    "amount_match": ("invoice_amount", "payment_amount"),
    "date_range": ("invoice_date", "travel_start_date", "travel_end_date"),
    "hotel_limit": ("invoice_amount", "policy_evidence"),
    "applicant_match": ("applicant_name",),
    "check_duplicate_invoice": ("invoice_number",),
}

STEP_REASONS: dict[str, str] = {
    "required_documents": "按报销类型核对必备材料清单",
    "amount_match": "确定性比对发票金额与付款金额",
    "date_range": "校验发票日期是否落在出差区间内",
    "hotel_limit": "依据制度条款校验住宿标准",
    "applicant_match": "跨文档对齐申请人/购买方/收款方",
    "check_duplicate_invoice": "查询历史记录,识别重复发票",
}

#: 步骤顺序:材料 -> 金额 -> 日期 -> 制度 -> 主体 -> 查重
STEP_ORDER: tuple[str, ...] = (
    "required_documents",
    "amount_match",
    "date_range",
    "hotel_limit",
    "applicant_match",
    "check_duplicate_invoice",
)


def allowed_tools() -> set[str]:
    """工具白名单(计划只能包含这些工具)。"""
    return set(TOOL_REGISTRY)


def build_plan(task_id: str, fields: dict | None = None, policy_evidence: list | None = None) -> AuditPlan:
    """按材料与字段状态生成受控审核计划。"""
    fields = fields or {}
    evidence = policy_evidence or []
    available = set(fields)
    if evidence:
        available.add("policy_evidence")

    steps: list[PlanStep] = []
    notes: list[str] = []
    for order, tool_name in enumerate(STEP_ORDER, start=1):
        if tool_name not in allowed_tools():
            notes.append(f"跳过未注册工具:{tool_name}")
            continue
        dependencies = STEP_FIELD_DEPENDENCIES.get(tool_name, ())
        missing = [name for name in dependencies if name not in available]
        steps.append(
            PlanStep(
                order=order,
                name=f"check_{tool_name}",
                tool=tool_name,
                reason=STEP_REASONS.get(tool_name, TOOL_REGISTRY[tool_name].spec.description),
                required=not missing,
                may_skip=bool(missing),
            )
        )
        if missing:
            notes.append(f"{tool_name} 缺少字段 {'、'.join(missing)}，该步可能无结论")

    return AuditPlan(task_id=task_id, version=1, created_by="rules", status="ready", steps=steps, notes=notes)


def validate_plan(plan: AuditPlan) -> list[str]:
    """校验计划合法性:所有步骤必须使用白名单工具且顺序唯一。"""
    issues: list[str] = []
    whitelist = allowed_tools()
    seen_orders: set[int] = set()
    for step in plan.steps:
        if step.tool not in whitelist:
            issues.append(f"步骤 {step.name} 使用了未注册工具 {step.tool}")
        if step.order in seen_orders:
            issues.append(f"步骤序号重复:{step.order}")
        seen_orders.add(step.order)
    return issues
