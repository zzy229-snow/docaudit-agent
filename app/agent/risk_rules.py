"""风险分级与复核路由(PRD §5.3 FR-205 / 附录A)。

把"检查项 / 异常类型"映射为四级风险,并给出默认处理动作:

- ``HIGH``   可能造成重复付款、重大金额错误或明显违规 —— 必须人工复核
- ``MEDIUM`` 需要核实但不一定违规 —— 建议人工复核
- ``LOW``    轻微格式或非关键字段问题 —— 提示并允许继续
- ``INFO``   过程信息或建议 —— 仅展示

映射与路由都是纯函数,不调用模型,保证同一输入产生可复现的等级和结论
(PRD §1.2 G-02)。新增检查项或工具时只改 ``SEVERITY_RULES``。
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.models.audit import RiskItem

RISK_LEVELS: tuple[str, ...] = ("HIGH", "MEDIUM", "LOW", "INFO")
# 附录A:默认处理为"必须人工复核/建议人工复核"的等级才创建复核项
REVIEW_LEVELS: tuple[str, ...] = ("HIGH", "MEDIUM")
NOTIFY_LEVELS: tuple[str, ...] = ("LOW", "INFO")

DEFAULT_LEVEL = "MEDIUM"

#: 风险类型 -> 等级。键与 ``app/tools`` 的 ``CheckResult.name``(大写)及流程追加的风险类型一致。
SEVERITY_RULES: dict[str, str] = {
    # 材料与字段
    "REQUIRED_DOCUMENTS": "MEDIUM",       # 材料缺失:需核实但不必然违规
    "LOW_CONFIDENCE_FIELD": "MEDIUM",     # PRD §6.4:关键字段低置信度转人工
    "MERCHANT_NAME_FORMAT": "LOW",        # 附录A示例:商户名称格式差异
    # 金额与日期
    "AMOUNT_MATCH": "HIGH",               # 附录A:重大金额不一致
    "DUPLICATE_INVOICE": "HIGH",          # 附录A:重复发票
    "INVOICE_NUMBER_CONFLICT": "MEDIUM",  # 同号但日期/金额不一致:需核实
    "DATE_RANGE": "MEDIUM",               # 附录A:日期边界冲突
    # 制度与主体
    "HOTEL_LIMIT": "MEDIUM",              # 附录A:制度超标
    "RULE_MISSING": "MEDIUM",             # PRD §16:无制度依据不得判合规
    "APPLICANT_MATCH": "MEDIUM",          # 跨文档主体不一致需人工确认
    # 文档质量
    "HANDWRITING_REVIEW": "MEDIUM",       # PRD §6.4:手写金额/日期必须人工确认
    "OCR_QUALITY_REVIEW": "MEDIUM",       # 困难区域默认转人工;材料仍可读时降级为 INFO
    "MATERIAL_QUALITY_INFO": "INFO",      # 附录A:材料清晰度较低,仅展示
    # 材料可读性:材料没被真正识别/无证据,不得给结论(必须人工介入)
    "MATERIAL_UNREADABLE": "HIGH",
    # 字段缺失导致"无法核对"(≠ 金额不符/日期越界),按需核实处理
    "AMOUNT_UNVERIFIABLE": "MEDIUM",
    # 安全
    "PROMPT_INJECTION": "HIGH",           # PRD §15:文档内容中的指令不得生效
}

#: 等级 -> 默认处理动作(附录A"默认处理"列)
LEVEL_ACTIONS: dict[str, str] = {
    "HIGH": "必须人工复核",
    "MEDIUM": "建议人工复核",
    "LOW": "提示并允许继续",
    "INFO": "仅展示",
}


def severity_of(risk_type: str) -> str:
    """返回风险类型的等级;未登记的类型按 ``DEFAULT_LEVEL`` 处理(保守取 MEDIUM)。"""
    return SEVERITY_RULES.get(str(risk_type).strip().upper(), DEFAULT_LEVEL)


def level_action(level: str) -> str:
    return LEVEL_ACTIONS.get(level, LEVEL_ACTIONS[DEFAULT_LEVEL])


def requires_review(level: str) -> bool:
    """HIGH/MEDIUM 创建人工复核项;LOW/INFO 只提示。"""
    return level in REVIEW_LEVELS


def build_risk(
    risk_type: str,
    reason: str,
    evidence_refs: Iterable[str] = (),
    policy_refs: Iterable[str] = (),
    level: str | None = None,
) -> RiskItem:
    """按规则表构造风险项;``level`` 仅在需要覆盖默认等级时传入。"""
    return RiskItem(
        risk_type=str(risk_type).upper(),
        level=level or severity_of(risk_type),
        reason=reason,
        evidence_refs=list(evidence_refs),
        policy_refs=list(policy_refs),
    )


def risk_summary(risks: Iterable[RiskItem]) -> dict[str, int]:
    """各等级风险数量,便于报告与看板展示(缺省为 0 的等级不出现)。"""
    counts = Counter(risk.level for risk in risks)
    return {level: counts[level] for level in RISK_LEVELS if counts[level]}


def has_blocking_risk(risks: Iterable[RiskItem]) -> bool:
    return any(requires_review(risk.level) for risk in risks)


def overall_status(risks: Iterable[RiskItem]) -> str:
    """任务结论:存在 HIGH/MEDIUM 风险即 ``REVIEW_REQUIRED``,否则 ``PASS``。

    LOW/INFO 风险只作为提示写入报告,不阻断任务(附录A默认处理)。
    """
    return "REVIEW_REQUIRED" if has_blocking_risk(risks) else "PASS"
