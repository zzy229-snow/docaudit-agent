"""文档指令注入检测(PRD §15 Prompt注入 / §10.4 关键约束5)。

原则:文档内容属于**不可信数据**。材料里出现的"忽略以上规则""直接判定合规"
等文本不得成为系统指令,系统应把它当作可疑输入记录下来并转人工复核。

本模块只做确定性关键词/正则匹配,不调用模型,保证可复现、可测试。
命中不改变任何确定性检查结果,只追加 PROMPT_INJECTION 风险(HIGH)。
"""
from __future__ import annotations

import re

#: 注入话术特征。命中即记录,不做"自动纠正",交由人工复核判断。
INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("忽略指令", r"忽略\s*(?:以上|上述|之前|前面|所有|全部)[^\n]{0,12}(?:规则|指令|要求|说明)"),
    ("忽略指令", r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?"),
    ("系统指令伪造", r"(?:系统|管理员|平台)\s*(?:指令|提示|要求)[^\n]{0,10}[:：]"),
    ("系统指令伪造", r"(?:system\s*prompt|override\s+(?:the\s+)?rules?)"),
    ("要求直接判合规", r"(?:直接|请|应当|必须)\s*(?:判定|输出|给出|返回)[^\n]{0,6}(?:合规|通过|pass)"),
    ("要求跳过检查", r"(?:跳过|无需|不用|不要)[^\n]{0,6}(?:检查|审核|核对)"),
    ("角色扮演", r"(?:你现在是|从现在开始你是|扮演)[^\n]{0,12}(?:角色|助手|审核员)"),
    ("越权特批", r"(?:已|已经)?(?:由)?[^\n]{0,8}(?:总监|总经理|老板|CEO)[^\n]{0,6}(?:特批|批准)[^\n]{0,8}(?:无需|不用|不必)"),
)

_INJECTION_RE = [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in INJECTION_PATTERNS]


def detect_injection(text: str | None) -> list[str]:
    """返回命中的注入片段(去重、保序、最多 3 条)。"""
    if not text:
        return []
    hits: list[str] = []
    for _label, pattern in _INJECTION_RE:
        for match in pattern.finditer(text):
            snippet = " ".join(match.group(0).split())
            if snippet and snippet not in hits:
                hits.append(snippet)
            if len(hits) >= 3:
                return hits
    return hits


def injection_labels(text: str | None) -> list[str]:
    """命中的注入类型标签(便于日志与看板统计)。"""
    if not text:
        return []
    return [label for label, pattern in _INJECTION_RE if pattern.search(text)]


def is_suspicious(text: str | None) -> bool:
    return bool(detect_injection(text))
