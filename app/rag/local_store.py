"""Local policy retrieval backend.

This backend is intentionally lightweight: it performs real retrieval over
policy chunks without requiring Milvus, bge-m3 or GPU. It is not a replacement
for a production vector database, but it removes the pure mock dependency and
gives the project a deterministic, testable RAG baseline.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.models.audit import PolicyEvidence
from app.rag.chunker import PolicyChunk, chunk_policy_documents
from app.config import repo_root


POLICY_DIR = repo_root() / "data" / "policies"


def retrieve_local(query: str, limit: int = 3) -> list[PolicyEvidence]:
    chunks = chunk_policy_documents(POLICY_DIR)
    if not query.strip() or not chunks:
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    doc_freq = _document_frequency(chunks)
    scored = []
    for chunk in chunks:
        score = _score(query_tokens, _tokenize(chunk.content + " " + chunk.section_path), doc_freq, len(chunks))
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        PolicyEvidence(
            chunk_id=chunk.chunk_id,
            section=chunk.section_path,
            content=chunk.content,
            score=round(score, 4),
        )
        for score, chunk in scored[:limit]
    ]


def _tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{1,4}", normalized)
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,4}", token):
            expanded.extend(token[i:i + 2] for i in range(len(token) - 1))
    return expanded


def _document_frequency(chunks: list[PolicyChunk]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for chunk in chunks:
        counter.update(set(_tokenize(chunk.content + " " + chunk.section_path)))
    return counter


def _score(query_tokens: list[str], doc_tokens: list[str], doc_freq: Counter[str], total_docs: int) -> float:
    doc_counts = Counter(doc_tokens)
    score = 0.0
    for token in query_tokens:
        tf = doc_counts[token]
        if tf <= 0:
            continue
        idf = math.log((total_docs + 1) / (doc_freq[token] + 1)) + 1
        score += (1 + math.log(tf)) * idf
    return score
