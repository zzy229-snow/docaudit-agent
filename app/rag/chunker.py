"""制度切片器(PRD §9.2)。

切片原则:
- 按章/条/款切分,保留父标题路径(section_path),不用固定字符数粗暴切分;
- 表格按行/逻辑组切分,保留表头对应关系;
- 每个 Chunk 保存 policy_id、version、department、expense_type、effective_from/to、section_path;
- 短标题/无实质内容条目跳过。

当前阶段制度来自 data/policies/*.json(协作基线格式:chunk_id/section/content)。
字段契约:检索器输出仍映射为 app.models.audit.PolicyEvidence(协作者基线字段,
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
    policy_id: str = "TRAVEL-V1"          # 制度标识(PRD §9.2)
    version: str = "V1"                   # 制度版本
    department: str = "ALL"               # 适用部门(ALL=全员,或具体部门名)
    expense_type: str = "TRAVEL"          # 费用类型:TRAVEL/PURCHASE 等
    effective_from: str = "2026-01-01"
    effective_to: str = ""                # 空=长期有效
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


def chunk_travel_policy(policy_path: Path) -> list[PolicyChunk]:
    """切片制度 JSON(当前协作基线的 data/policies/travel_policy.json 格式)。

    条目结构 {chunk_id, section, content};section 即节号,section_path 额外补标题。
    """
    rows = json.loads(policy_path.read_text(encoding="utf-8"))
    chunks = []
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        section = (row.get("section") or "").strip()
        chunks.append(PolicyChunk(
            chunk_id=row["chunk_id"],
            section_path=f"{section} 住宿标准",
            content=content,
            metadata={"source": str(policy_path.name)},
        ))
    return chunks


def chunk_policy_documents(policy_dir: Path) -> list[PolicyChunk]:
    """从 data/policies/ 目录加载全部制度并切片。"""
    chunks = []
    for f in sorted(policy_dir.glob("*.json")):
        if f.name == "README.md":
            continue
        chunks.extend(chunk_travel_policy(f))
    return chunks
