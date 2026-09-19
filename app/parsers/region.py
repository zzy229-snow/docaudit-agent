"""困难区域与手写识别评估(PRD §6.2 / §8.1,任务④)。

两类评估:
- assess_image():基于图片质量(分辨率/对比度/尺寸)判断是否为困难材料;
- assess_text():基于 OCR 文本质量(乱码率/手写关键词/字段完整性)判断。

输出 RegionAssessment,供路由(选高精度引擎)与审核流程(标记人工复核)使用。
本模块不修改 app/models 契约:难度以 document_type 值后缀与审核风险项体现。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

LEVEL_NORMAL = "normal"
LEVEL_HARD = "hard"
LEVEL_HANDWRITING = "handwriting"

# 手写/盖章相关关键词:命中说明材料含手写或印章区域,识别可靠性下降
HANDWRITING_KEYWORDS = ("签字", "签名", "手写", "盖章", "印章", "按手印", "签署")

# 正常中文字符/数字/常见标点(用于乱码率计算)
_MEANINGFUL = re.compile(r"[\u4e00-\u9fffA-Za-z0-9\s，。：；、（）()%¥￥\-—.,:;/\\'\"*#&+]")

LOW_RESOLUTION_LONG_EDGE = 800     # 长边小于此值视为低分辨率
LOW_CONTRAST_STDDEV = 15.0         # 灰度标准差低于此值视为低对比度(实测:单行文字≈24,灰字≈6,纯色=0)
MIN_TEXT_CHARS = 10                # 少于此字符数视为文本过少


@dataclass
class RegionAssessment:
    level: str = LEVEL_NORMAL
    reasons: list[str] = field(default_factory=list)
    needs_human_review: bool = False
    suggested_engine: str = "tesseract"

    @property
    def is_hard(self) -> bool:
        return self.level in (LEVEL_HARD, LEVEL_HANDWRITING)


def assess_image(content: bytes) -> RegionAssessment:
    """按图片质量评估困难程度(分辨率/对比度)。"""
    from PIL import Image, ImageStat

    reasons: list[str] = []
    try:
        with Image.open(BytesIO(content)) as img:
            gray = img.convert("L")
            width, height = gray.size
            stat = ImageStat.Stat(gray)
            stddev = stat.stddev[0] if stat.stddev else 0.0
    except Exception as exc:  # noqa: BLE001 — 无法解析的图片本身即困难材料
        return RegionAssessment(level=LEVEL_HARD, reasons=[f"图片无法解析:{exc}"],
                                needs_human_review=True, suggested_engine="mineru")

    long_edge = max(width, height)
    if long_edge < LOW_RESOLUTION_LONG_EDGE:
        reasons.append(f"分辨率偏低({width}x{height},长边<{LOW_RESOLUTION_LONG_EDGE})")
    if stddev < LOW_CONTRAST_STDDEV:
        reasons.append(f"对比度偏低(灰度标准差{stddev:.1f})")

    if reasons:
        return RegionAssessment(level=LEVEL_HARD, reasons=reasons,
                                needs_human_review=True, suggested_engine="mineru")
    return RegionAssessment(level=LEVEL_NORMAL, reasons=[f"图片质量正常({width}x{height})"],
                            needs_human_review=False, suggested_engine="tesseract")


def assess_text(text: str) -> RegionAssessment:
    """按 OCR 文本质量评估困难程度(乱码率/手写关键词/文本长度)。"""
    stripped = (text or "").strip()
    reasons: list[str] = []

    if len(stripped) < MIN_TEXT_CHARS:
        return RegionAssessment(level=LEVEL_HARD, reasons=[f"识别文本过少({len(stripped)}字符)"],
                                needs_human_review=True, suggested_engine="mineru")

    matched = len(_MEANINGFUL.findall(stripped))
    garbled_ratio = 1 - matched / max(len(stripped), 1)
    if garbled_ratio > 0.3:
        reasons.append(f"乱码率偏高({garbled_ratio:.0%})")

    hits = [kw for kw in HANDWRITING_KEYWORDS if kw in stripped]
    if hits:
        reasons.append(f"含手写/盖章关键词({'、'.join(hits)})")
        return RegionAssessment(level=LEVEL_HANDWRITING, reasons=reasons,
                                needs_human_review=True, suggested_engine="mineru")

    if reasons:
        return RegionAssessment(level=LEVEL_HARD, reasons=reasons,
                                needs_human_review=True, suggested_engine="mineru")
    return RegionAssessment(level=LEVEL_NORMAL, reasons=["文本质量正常"],
                            needs_human_review=False, suggested_engine="tesseract")


def assess(content: bytes | None = None, text: str | None = None) -> RegionAssessment:
    """综合图片质量与文本质量评估;两者都困难时取更严重的等级。"""
    assessments = []
    if content is not None:
        assessments.append(assess_image(content))
    if text is not None:
        assessments.append(assess_text(text))
    if not assessments:
        return RegionAssessment()
    # handwriting > hard > normal
    rank = {LEVEL_NORMAL: 0, LEVEL_HARD: 1, LEVEL_HANDWRITING: 2}
    worst = max(assessments, key=lambda a: rank.get(a.level, 0))
    reasons = [r for a in assessments for r in a.reasons]
    return RegionAssessment(
        level=worst.level,
        reasons=reasons,
        needs_human_review=any(a.needs_human_review for a in assessments),
        suggested_engine="mineru" if worst.is_hard else "tesseract",
    )


def doc_type_suffix(assessment: RegionAssessment) -> str:
    """把难度编码为 document_type 后缀(不改 Document 契约)。"""
    if assessment.level == LEVEL_HANDWRITING:
        return "_handwriting"
    if assessment.level == LEVEL_HARD:
        return "_hard"
    return ""


def human_review_reason(assessment: RegionAssessment, file_name: str) -> str:
    """生成人工复核提示文本。"""
    detail = "、".join(assessment.reasons) if assessment.reasons else "识别质量不足"
    return f"{file_name} 含困难区域({assessment.level}):{detail},建议人工核对原文"


# 报销材料的核心字段关键词:命中越多说明识别越有效
FIELD_KEYWORDS = ("发票", "金额", "日期", "付款", "申请人", "城市", "号码", "购买方", "收款方", "出差")


def text_quality_score(text: str) -> float:
    """OCR 文本质量评分 0~1(任务④:多策略择优用)。

    组成:乱码率(权重 0.6) + 核心字段关键词命中率(权重 0.4)。
    识别成功但内容稀少的文本得分低,可据此触发换策略重试。
    """
    stripped = (text or "").strip()
    if not stripped:
        return 0.0
    matched = len(_MEANINGFUL.findall(stripped))
    clean_ratio = matched / max(len(stripped), 1)
    hits = sum(1 for kw in FIELD_KEYWORDS if kw in stripped)
    keyword_ratio = min(hits / 3.0, 1.0)
    return round(0.6 * clean_ratio + 0.4 * keyword_ratio, 4)
