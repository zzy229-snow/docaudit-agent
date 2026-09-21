"""材料可读性判定:读不出来就不给结论(AC-05 的对称面)。

背景(实机踩到的坑):默认配置是"离线安全模式"——``OCR_ENGINE=mock`` 时
图片/扫描件根本不会被识别,解析器会返回一段固定的演示文本,后续的字段抽取、
六项确定性检查、风险分级全部建立在这段假文本上,于是出现"上传的是 A 发票,
结论却是 B 发票"的误导性结果。这比"读错"更危险,因为它看起来是有效结论。

判定依据是"解析结果本身",而不是配置项:MockOcrEngine 产出的文本带固定标记
(``【OCR-MOCK】``),真 OCR(Tesseract / HTTP / MinerU)与评测用合成语料引擎
(stub)都不带。这样既不会被"配了个假引擎"绕过,也不会误伤测试替身。

判定为"不可读"的三种情形:
1. 材料文本是演示引擎产出的固定文本(带标记)—— 内容与上传材料无关;
2. 全部材料都解析不出有效文本(空或短于 ``MATERIAL_MIN_TEXT_CHARS``);
3. 材料解析出来了,但一个可用字段都没抽到(无证据即无结论,PRD §16)。

命中任一情形时,审核流程应把结论标记为"无法判定",并用人工可操作的语言说明
原因与改法,而不是输出"通过 / 需复核"。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

#: 文本长度下限:低于该长度的解析结果视为未识别出有效内容
DEFAULT_MIN_TEXT_CHARS = 12

#: 演示/合成引擎的文本标记(MockOcrEngine.MOCK_TEXT 的开头)
SYNTHETIC_TEXT_MARKERS: tuple[str, ...] = ("【OCR-MOCK】", "【OCR-STUB】")

#: 不真正读取材料的引擎名(界面提示用;判定"是否可读"看文本标记,不看这里)
SYNTHETIC_OCR_ENGINES: frozenset[str] = frozenset({"", "mock", "stub", "none", "off"})

#: 需要 OCR 才能得到文本的文档类型前缀(见 ``app/parsers/loader.parse_document``)
OCR_DOCUMENT_PREFIXES: tuple[str, ...] = ("image_ocr", "pdf_ocr")


def min_text_chars() -> int:
    try:
        return max(0, int(os.environ.get("MATERIAL_MIN_TEXT_CHARS", str(DEFAULT_MIN_TEXT_CHARS))))
    except ValueError:
        return DEFAULT_MIN_TEXT_CHARS


def ocr_engine_name() -> str:
    """当前配置的 OCR 引擎名(与 ``app.parsers.ocr.get_ocr_engine`` 同一环境变量)。"""
    return os.environ.get("OCR_ENGINE", "mock").strip().lower()


def model_provider_name() -> str:
    return os.environ.get("MODEL_PROVIDER", "mock").strip().lower()


def is_synthetic_engine(engine: str | None = None) -> bool:
    """引擎是否属于"不真正读取材料"的实现。"""
    return (engine if engine is not None else ocr_engine_name()) in SYNTHETIC_OCR_ENGINES


def needs_ocr(document_type: str | None) -> bool:
    """该文档类型是否由 OCR 产出(图片、扫描件 PDF)。"""
    text = str(document_type or "")
    return any(text.startswith(prefix) for prefix in OCR_DOCUMENT_PREFIXES)


def is_synthetic_text(text: str | None) -> bool:
    """解析出的文本是否来自演示/合成引擎。"""
    content = text or ""
    return any(marker in content for marker in SYNTHETIC_TEXT_MARKERS)


def demo_mode_notice() -> str | None:
    """演示模式提示(界面横幅用);不在演示模式时返回 ``None``。"""
    parts: list[str] = []
    if is_synthetic_engine():
        parts.append(f"OCR 引擎={ocr_engine_name()}（不识别图片/扫描件）")
    if is_synthetic_engine(model_provider_name()):
        parts.append(f"模型={model_provider_name()}（不调用真实模型）")
    if not parts:
        return None
    return (
        "当前为演示模式：" + "；".join(parts) +
        "。上传真实发票/扫描件不会得到真实结论（会被标记为“无法判定”），请配置真实引擎后再做验收。"
    )


def is_human_provided_field(item: object) -> bool:
    """字段是否来自人工填写/修正(而不是从材料里抽出来的)。

    人工修正沿用原字段的 ``document_id``,只把 ``source_text`` 改成"人工修正：…";
    人工补充的新字段 ``document_id`` 为 ``"manual"``。两者都算人的判断。
    """
    if getattr(item, "document_id", "") == "manual":
        return True
    source = str(getattr(item, "source_text", "") or "")
    return source.startswith("人工修正") or source.startswith("人工补充")


def human_provided_fields(fields: dict | None) -> dict:
    """筛出人工填写/修正的字段(材料未识别时,只有这些还站得住)。"""
    return {name: item for name, item in (fields or {}).items() if is_human_provided_field(item)}


@dataclass(frozen=True)
class ReadabilityVerdict:
    """材料可读性结论。"""

    readable: bool
    reason: str | None = None
    engine: str = "mock"
    #: 判定为不可读的材料名
    unreadable_files: list[str] = field(default_factory=list)
    #: 机器可读的原因码:MOCK_OCR | NO_TEXT | NO_FIELDS
    code: str | None = None

    def as_trace(self) -> str:
        if self.readable:
            return f"readability: 材料可读（OCR引擎={self.engine}）"
        return f"readability: 材料不可读（{self.code}，OCR引擎={self.engine}）——结论标记为无法判定"


def assess_readability(documents: list, fields: dict | None = None) -> ReadabilityVerdict:
    """判定材料是否被真正读取到可以出结论的程度。

    ``documents`` 为 ``app.models.document.Document`` 列表,``fields`` 为抽取结果字典。
    """
    engine = ocr_engine_name()
    documents = list(documents or [])
    if not documents:
        return ReadabilityVerdict(
            readable=False,
            code="NO_TEXT",
            reason="未收到任何可解析的材料，无法作出审核结论。",
            engine=engine,
        )

    # 1) 解析结果是演示引擎的固定文本 —— 内容与上传材料无关
    synthetic = [doc for doc in documents if is_synthetic_text(getattr(doc, "text", ""))]
    if synthetic:
        names = "、".join(getattr(doc, "file_name", "未知文件") for doc in synthetic)
        return ReadabilityVerdict(
            readable=False,
            code="MOCK_OCR",
            reason=(
                f"{names} 的内容并未被真正识别：当前 OCR 引擎是演示模式（OCR_ENGINE={engine}），"
                "系统解析出的是内置演示文本，与上传材料无关。因此不能对该材料作出审核结论。"
                "请在 .env 配置真实 OCR 引擎（OCR_ENGINE=tesseract|http|mineru）后重新提交。"
            ),
            engine=engine,
            unreadable_files=[getattr(doc, "file_name", "未知文件") for doc in synthetic],
        )

    # 2) 全部材料都没解析出有效文本
    threshold = min_text_chars()
    short = [getattr(doc, "file_name", "未知文件") for doc in documents
             if len((getattr(doc, "text", "") or "").strip()) < threshold]
    if len(short) == len(documents):
        names = "、".join(short)
        return ReadabilityVerdict(
            readable=False,
            code="NO_TEXT",
            reason=(
                f"全部材料（{names}）均未解析出有效文本（不足 {threshold} 字），"
                "系统读不到材料内容，无法作出审核结论。请确认文件完整、清晰，"
                "或更换识别引擎后重新提交。"
            ),
            engine=engine,
            unreadable_files=short,
        )

    # 3) 有文本但一个字段都没抽到 —— 无证据即无结论(PRD §16)
    if not (fields or {}):
        return ReadabilityVerdict(
            readable=False,
            code="NO_FIELDS",
            reason=(
                "材料已解析出文本，但未抽取到任何可用字段（金额、日期、发票号等），"
                "没有可用于确定性检查的证据，无法作出审核结论。"
                "请补充材料或人工修正字段后重跑。"
            ),
            engine=engine,
            unreadable_files=[getattr(doc, "file_name", "未知文件") for doc in documents],
        )

    return ReadabilityVerdict(readable=True, engine=engine)
