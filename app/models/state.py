from time import perf_counter
from typing import Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field


AgentNodeStatus = Literal["started", "completed", "failed", "skipped"]


class AgentTraceEvent(BaseModel):
    node: str
    status: AgentNodeStatus
    message: str
    elapsed_ms: float | None = None


class ToolCallRecord(BaseModel):
    tool_name: str
    status: Literal["success", "failed", "skipped"]
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)


class HumanReviewItem(BaseModel):
    reason: str
    severity: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    evidence_refs: list[str] = Field(default_factory=list)


class AgentControl(BaseModel):
    max_steps: int = Field(default=10, ge=1)
    step_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    terminated_reason: str | None = None


def new_task_id() -> str:
    return f"audit-{uuid4().hex[:12]}"


def start_node(state: "AuditState", node: str, message: str) -> float:
    state.setdefault("agent_trace", []).append(AgentTraceEvent(node=node, status="started", message=message))
    return perf_counter()


def finish_node(state: "AuditState", node: str, started_at: float, message: str) -> None:
    elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
    state.setdefault("agent_trace", []).append(
        AgentTraceEvent(node=node, status="completed", message=message, elapsed_ms=elapsed_ms)
    )
    state.setdefault("trace", []).append(f"{node}: {message} ({elapsed_ms}ms)")


def record_tool_call(state: "AuditState", tool_name: str, status: str, summary: str, evidence_refs: list[str] | None = None) -> None:
    state.setdefault("tool_calls", []).append(
        ToolCallRecord(
            tool_name=tool_name,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            evidence_refs=evidence_refs or [],
        )
    )


def require_next_step(state: "AuditState", node: str) -> None:
    control = state.setdefault("control", AgentControl())
    if control.terminated_reason:
        raise RuntimeError(control.terminated_reason)
    if control.step_count >= control.max_steps:
        control.terminated_reason = f"Agent reached max_steps={control.max_steps} before node={node}"
        raise RuntimeError(control.terminated_reason)
    control.step_count += 1


class AuditState(TypedDict, total=False):
    task_id: str
    current_node: str
    files: list[tuple[str, bytes]]
    documents: list[Any]
    fields: dict[str, Any]
    policy_evidence: list[Any]
    checks: list[Any]
    risks: list[Any]
    human_review_items: list[HumanReviewItem]
    tool_calls: list[ToolCallRecord]
    agent_trace: list[AgentTraceEvent]
    control: AgentControl
    trace: list[str]
    report: Any
