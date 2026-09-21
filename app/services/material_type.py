"""材料类型判定:按**票面内容**判断这份材料是发票 / 付款凭证 / 审批单。

为什么需要它:客户上传的文件名常常是「微信图片_2026.png」「扫描件001.pdf」,只按文件名
找关键词会把"明明识别出内容的发票"算成"缺少发票"。

判定采用两路证据,内容优先:

1. **内容信号(强)**:票面上的固定词,如 发票号码/价税合计/增值税 → 发票,
   付款金额/流水号/转账 → 付款凭证,审批意见/审批人/同意 → 审批单;
   OCR 接口的结构化字段(如 ``invoice_number``/``invoice_amount``)同样算内容信号。
2. **文件名关键词(弱,兜底)**:``invoice``/``发票``、``payment``/``付款``、``approval``/``审批``。

只有一个类型的信号**既达到最低条数、又领先第二名至少 1 条**时才判定为该类型(避免一段
什么都沾的演示文本被算成"三类齐全"从而掩盖缺材料);内容不够决定性就看文件名;都判不出
则返回 ``other``(不明)。判定是纯函数,不调用模型,同输入同结论。
"""
from __future__ import annotations

import os
from collections.abc import Mapping

#: 材料类型(与 required_documents 检查项一致)
MATERIAL_TYPES: tuple[str, ...] = ("invoice", "payment", "approval")

#: 类型 → 中文标签(拼进材料标识供 required_documents 使用)
MATERIAL_LABELS: dict[str, str] = {
    "invoice": "发票",
    "payment": "付款凭证",
    "approval": "审批单",
}

#: 票面内容信号
CONTENT_SIGNALS: dict[str, tuple[str, ...]] = {
    "invoice": ("发票代码", "发票号码", "价税合计", "增值税", "开票日期", "校验码", "发票专用章"),
    "payment": ("付款金额", "支付金额", "付款时间", "支付时间", "付款凭证", "流水号", "交易流水", "转账"),
    "approval": ("审批意见", "审批人", "审批单", "审批通过", "出差审批", "报销申请", "申请单", "同意"),
}

#: OCR 接口结构化字段 → 材料类型
STRUCTURED_FIELD_SIGNALS: dict[str, tuple[str, ...]] = {
    "invoice": ("invoice_code", "invoice_number", "invoice_date", "invoice_amount", "invoice_buyer"),
    "payment": ("payment_amount", "payment_party"),
}

#: 文件名关键词(弱证据)
FILENAME_HINTS: dict[str, tuple[str, ...]] = {
    "invoice": ("invoice", "发票"),
    "payment": ("payment", "付款", "支付", "流水"),
    "approval": ("approval", "审批", "申请单"),
}

#: 内容信号最低条数(低于此数不看内容)
DEFAULT_MIN_CONTENT_SIGNALS = 2


def min_content_signals() -> int:
    try:
        return max(1, int(os.environ.get("MATERIAL_MIN_CONTENT_SIGNALS", str(DEFAULT_MIN_CONTENT_SIGNALS))))
    except ValueError:
        return DEFAULT_MIN_CONTENT_SIGNALS


def content_scores(text: str | None, structured_fields: Mapping[str, object] | None = None) -> dict[str, int]:
    """各类型的内容信号得分(文本命中 + 结构化字段命中)。"""
    haystack = text or ""
    fields = {str(name).lower() for name in (structured_fields or {})}
    scores: dict[str, int] = {}
    for material, signals in CONTENT_SIGNALS.items():
        score = sum(1 for signal in signals if signal in haystack)
        score += sum(1 for field in STRUCTURED_FIELD_SIGNALS.get(material, ()) if field in fields)
        scores[material] = score
    return scores


def filename_type(file_name: str | None) -> str | None:
    """按文件名关键词判断类型(弱证据);判不出返回 ``None``。"""
    name = str(file_name or "").lower()
    for material, keywords in FILENAME_HINTS.items():
        if any(keyword.lower() in name for keyword in keywords):
            return material
    return None


def classify_material(file_name: str | None, text: str | None = "",
                      structured_fields: Mapping[str, object] | None = None) -> str:
    """判定单份材料的类型;判不出返回 ``"other"``。

    内容判定要求"决定性":胜出类型达到最低条数,且第二名**也低于**最低条数。
    一段同时含发票与付款信息的材料(例如演示语料)不算决定性,回退文件名,
    避免把"三类齐全"判出来从而掩盖真正的缺材料。
    """
    threshold = min_content_signals()
    scores = content_scores(text, structured_fields)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    if best_score >= threshold and second_score < threshold:
        return best
    return filename_type(file_name) or "other"


def material_check_inputs(documents: list, structured_fields: Mapping[str, object] | None = None
                          ) -> tuple[list[str], list[str]]:
    """构造 required_documents 的入参:**内容优先**。

    返回 ``(names_for_keyword_fallback, content_types)``:
    - 内容判得出类型的材料 → 只把类型放进 ``content_types``(不再传文件名,避免文件名误判);
    - 内容判不出类型的材料 → 把文件名放进 ``names_for_keyword_fallback`` 做关键词兜底。
    """
    names: list[str] = []
    types: list[str] = []
    for doc in documents or []:
        name = str(getattr(doc, "file_name", ""))
        material = classify_material(name, getattr(doc, "text", ""), structured_fields)
        if material in MATERIAL_LABELS:
            types.append(material)
        else:
            names.append(name)
    return names, types


def classify_documents(documents: list, structured_fields: Mapping[str, object] | None = None) -> dict[str, str]:
    """批量判定(含文件名兜底),用于报告/轨迹展示:``{文件名: 类型}``。"""
    return {
        str(getattr(doc, "file_name", "")): classify_material(
            getattr(doc, "file_name", ""), getattr(doc, "text", ""), structured_fields)
        for doc in documents or []
    }
