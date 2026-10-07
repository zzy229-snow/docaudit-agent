"""制度切片器(PRD §9.2)。

切片原则:
- 按章/条/款切分,保留父标题路径(section_path),不用固定字符数粗暴切分;
- 表格按行切分,保留表头对应关系(任务⑥:表格切片);
- 每个 Chunk 保存 policy_id、version、department、expense_type、effective_from/to、section_path;
- 短标题/无实质内容条目跳过。

当前阶段制度来自 data/policies/*.json(协作基线格式:chunk_id/section/content,
任务⑥扩充为多费用类型制度,如 EXPENSE/MEDICAL/PURCHASE)。
字段契约:检索器输出仍映射为 app.models.audit.PolicyEvidence(既有契约字段,
不新增契约字段;policy_id/version/department 等只存 Milvus metadata 内部使用)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass
class PolicyChunk:
    chunk_id: str
    section_path: str          # 父标题路径,如 "4.2 住宿标准"
    content: str
    policy_id: str = "TRAVEL"          # 制度标识(PRD §9.2)
    version: str = "V1"                # 制度版本
    department: str = "ALL"            # 适用部门(ALL=全员,或具体部门名)
    expense_type: str = "TRAVEL"       # 费用类型:TRAVEL/EXPENSE/MEDICAL/PURCHASE
    effective_from: str = "2026-01-01"
    effective_to: str = ""             # 空=长期有效
    metadata: dict = field(default_factory=dict)

    def to_milvus_row(self, vector: list[float], sparse: dict) -> dict:
        """转为 Milvus 插入行:稠密+稀疏向量与元数据。"""
        return {
            "vector": vector,
            "sparse_vector": sparse,
            "chunk_id": self.chunk_id,
            "section_path": self.section_path,
            "content": self.content,
            "policy_id": self.policy_id,
            "version": self.version,
            "department": self.department,
            "expense_type": self.expense_type,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
        }


# chunk_id 前缀 -> (policy_id, expense_type)
POLICY_META = {
    "TRAVEL": ("TRAVEL", "TRAVEL"),
    "EXP": ("EXPENSE", "EXPENSE"),
    "PHARM": ("MEDICAL", "MEDICAL"),
    "OFFICE": ("PURCHASE", "PURCHASE"),
}


def _infer_policy(kid: str) -> tuple[str, str, str, str]:
    """从 chunk_id 前缀推断 (policy_id, version, department, expense_type)。

    约定: <POLICY>-<VERSION>-<SECTION>(如 TRAVEL-V1-4.2 或 OFFICE-V2-2.1)。
    表格内行级 department 由 _row_department 在切片时覆盖。
    """
    kid = (kid or "").upper()
    prefix = kid.split("-", 1)[0] if "-" in kid else "TRAVEL"
    version = "V1" if "V1" in kid else "V2" if "V2" in kid else "V1"
    policy_id, expense_type = POLICY_META.get(prefix, ("TRAVEL", "TRAVEL"))
    return policy_id, version, "ALL", expense_type


def _row_department(row_text: str) -> str:
    """从表格行文本提取适用部门(若非 ALL)。"""
    low = row_text
    for dept in ("市场部", "销售部", "研发部", "技术部"):
        if dept in low:
            return dept
    return "ALL"


def _split_table(content: str) -> list[tuple[str, str]]:
    """把表格内容(markdown 竖线)按数据行切开,保留表头。

    返回 [(行chunk_id, 行文本)],行文本 = 表头 + 该行,便于检索命中具体行。
    非表格内容原样返回 [("", content)]。
    """
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    # 定位含 "|" 的表头行:跳过表格标题(如 "表4-1…")和 markdown 分隔行(---)
    data_lines = [ln for ln in lines if "---" not in ln and "|" in ln]
    if len(data_lines) < 2:
        return [("", content)]
    header = " ｜ ".join(c.strip() for c in data_lines[0].strip("|").split("|"))
    parts = []
    for i, line in enumerate(data_lines[1:], start=1):
        if "|" not in line:
            continue
        row = " ｜ ".join(c.strip() for c in line.strip("|").split("|"))
        parts.append((f"-R{i}", f"{header}｜{row}"))
    return parts if parts else [("", content)]


def _chunk_json_file(path: Path):
    """切片单个制度 JSON(协作基线格式 + 扩充政策),返回 PolicyChunk 列表。"""
    rows = json.loads(path.read_text(encoding="utf-8"))
    chunks = []
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        section = (row.get("section") or "").strip()
        kid = row.get("chunk_id", "")
        policy_id, version, _, expense_type = _infer_policy(kid)
        for suffix, sub in _split_table(content):
            eff_dept = _row_department(sub) if suffix else "ALL"
            chunks.append(PolicyChunk(
                chunk_id=kid + suffix,
                section_path=f"{section}",
                content=sub,
                policy_id=policy_id,
                version=version,
                department=eff_dept,
                expense_type=expense_type,
                metadata={"source": str(path.name)},
            ))
    return chunks


def chunk_policy_documents(policy_dir: Path) -> list[PolicyChunk]:
    """从 data/policies/ 目录加载全部制度并切片(含表格行拆分)。"""
    chunks = []
    for f in sorted(policy_dir.glob("*.json")):
        try:
            chunks.extend(_chunk_json_file(f))
        except json.JSONDecodeError as exc:
            print(f"[chunker] 跳过 {f.name}: JSON 解析失败 {exc}")
    return chunks
