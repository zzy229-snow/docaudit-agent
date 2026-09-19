"""上传制度的文本切片(PRD §9.1/§9.2)。

与 ``chunker.py``(读取 ``data/policies/*.json`` 基线文件)互补:本模块处理
**上传的原始制度文件**(TXT/DOCX/PDF 解析后的文本),产出同一 ``PolicyChunk`` 契约。

切片策略(§9.2):
- 按"章/条/款"与编号标题切分,保留父标题路径 ``section_path``;
- 表格按行切分并保留表头(住宿标准等表格不得拆掉城市与金额的对应关系);
- 过短段落与当前父标题合并,避免碎片;
- 每个 chunk 带 policy_id/version/department/expense_type/effective_from/to 元数据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.chunker import PolicyChunk

#: 章节标题:第4章 / 第4.2条 / 4.2 / 二、住宿标准
_HEADING_RE = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*[章节条款]|\d+(?:\.\d+)*[、.\s]|[一二三四五六七八九十]+、)\s*(?P<title>\S.*)$"
)
#: 章级标题(作为父标题路径)
_CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*章|[一二三四五六七八九十]+、|\d+\s*[、.]?\s*(?![\d.]))"
)
_MIN_CHUNK_CHARS = 12


@dataclass
class _Section:
    path: str
    lines: list[str]


def _is_heading(line: str) -> str | None:
    match = _HEADING_RE.match(line)
    if not match:
        return None
    return line.strip()[:60]


def _is_chapter(heading: str) -> bool:
    return bool(_CHAPTER_RE.match(heading))


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and set(stripped) <= set("|-: ")


def _split_sections(text: str) -> list[_Section]:
    """按标题切分为小节;标题行本身作为小节首行,保证短条款也能成块。"""
    sections: list[_Section] = []
    current: _Section | None = None
    parent = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading = _is_heading(line)
        if heading:
            if current is not None and any(item.strip() for item in current.lines):
                sections.append(current)
            if _is_chapter(heading):
                parent = heading
                path = heading
            else:
                path = f"{parent}·{heading}" if parent else heading
            current = _Section(path=path, lines=[line])
            continue
        if current is None:
            current = _Section(path=parent or "正文", lines=[])
        current.lines.append(line)
    if current is not None and any(item.strip() for item in current.lines):
        sections.append(current)
    return sections


def _table_header(lines: list[str], row_index: int) -> str:
    """取表格表头(本小节内第一个以 | 分隔的非分隔行)。"""
    for line in lines[:row_index]:
        if "|" in line and not _is_separator(line):
            return line.strip()
    return ""


def _chunks_from_section(section: _Section) -> list[tuple[str, str]]:
    """把一个小节切成 (子标题, 内容) 列表:表格按行切分并保留表头。"""
    chunks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        content = " ".join(item.strip() for item in paragraph if item.strip())
        if len(content) >= _MIN_CHUNK_CHARS:
            chunks.append(("", content))
        elif content and chunks and chunks[-1][1]:
            # 过短内容并入上一块,避免碎片化
            chunks[-1] = (chunks[-1][0], f"{chunks[-1][1]} {content}")

    for index, line in enumerate(section.lines):
        if "|" in line:
            flush_paragraph()
            paragraph = []
            if _is_separator(line):
                continue
            header = _table_header(section.lines, index)
            if not header:
                # 本小节第一行表格:若紧跟分隔行,说明它是表头,只作为表头随数据入块
                next_line = section.lines[index + 1] if index + 1 < len(section.lines) else ""
                if _is_separator(next_line):
                    continue
            if header and header == line.strip():
                continue  # 表头单独成行:随行数据一起入块
            content = f"{header}\n{line.strip()}" if header else line.strip()
            chunks.append(("表格行", content))
            continue
        if not line.strip():
            flush_paragraph()
            paragraph = []
            continue
        paragraph.append(line)
    flush_paragraph()
    return chunks


def chunk_text(
    text: str,
    *,
    policy_id: str,
    version: str = "V1",
    department: str = "ALL",
    expense_type: str = "TRAVEL",
    effective_from: str = "2026-01-01",
    effective_to: str = "",
) -> list[PolicyChunk]:
    """把制度正文切成带完整元数据的 chunk 列表。"""
    chunks: list[PolicyChunk] = []
    index = 0
    for section in _split_sections(text or ""):
        for sub_title, content in _chunks_from_section(section):
            index += 1
            section_path = section.path if not sub_title else f"{section.path}·{sub_title}"
            chunks.append(
                PolicyChunk(
                    chunk_id=f"{policy_id}-{version}-{index:03d}",
                    section_path=section_path,
                    content=content,
                    policy_id=policy_id,
                    version=version,
                    department=department or "ALL",
                    expense_type=expense_type or "TRAVEL",
                    effective_from=effective_from or "",
                    effective_to=effective_to or "",
                )
            )
    return chunks
