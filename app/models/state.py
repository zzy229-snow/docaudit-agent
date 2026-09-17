from typing import TypedDict, Any


class AuditState(TypedDict, total=False):
    files: list[tuple[str, bytes]]
    documents: list[Any]
    fields: dict[str, Any]
    policy_evidence: list[Any]
    checks: list[Any]
    risks: list[Any]
    trace: list[str]
    report: Any
