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
    input_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class HumanReviewItem(BaseModel):
    reason: str
    severity: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    evidence_refs: list[str] = Field(default_factory=list)


class AgentControl(BaseModel):
    """运行时预算与防循环控制(PRD §7.4)。"""

    max_steps: int = Field(default=10, ge=1)
    step_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    #: 单工具重试上限(指数退避)
    max_tool_retries: int = Field(default=2, ge=0)
    tool_retry_count: int = Field(default=0, ge=0)
    #: 单任务超时(秒),PRD §7.4 默认 120 秒
    timeout_seconds: float = Field(default=120.0, gt=0)
    #: Token 预算;0 = 未启用。当前网关不返回 usage,字段留待接入后启用(docs/AGENT_WORKFLOW.md)
    token_budget: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    #: 安全终止原因(达到步数/超时/无进展/预算耗尽)
    terminated_reason: str | None = None
    #: 路由结论:continue / review / fail(PRD §7.2 route_review)
    route: str | None = None
    route_reason: str | None = None


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


def record_tool_call(
    state: "AuditState",
    tool_name: str,
    status: str,
    summary: str,
    evidence_refs: list[str] | None = None,
    input_summary: dict[str, Any] | None = None,
) -> None:
    state.setdefault("tool_calls", []).append(
        ToolCallRecord(
            tool_name=tool_name,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            input_summary=input_summary or {},
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


def record_tool_retry(state: "AuditState", tool_name: str, attempt: int, reason: str) -> None:
    """记录一次工具重试(PRD §7.4 单工具重试次数,指数退避)。"""
    control = state.setdefault("control", AgentControl())
    control.tool_retry_count += 1
    control.retry_count += 1
    state.setdefault("trace", []).append(f"tool_retry: {tool_name} 第{attempt}次重试（{reason}）")


def elapsed_seconds(state: "AuditState") -> float:
    """任务已耗时(秒);未记录起点时按 0 处理。"""
    started_at = state.get("started_at")
    return round(perf_counter() - started_at, 3) if started_at else 0.0


def no_progress_detected(state: "AuditState", threshold: int = 2) -> bool:
    """相同工具 + 相同输入连续出现 ``threshold`` 次 → 判定无进展(PRD §7.4)。"""
    calls = state.get("tool_calls") or []
    if len(calls) < threshold:
        return False
    tail = calls[-threshold:]
    first = tail[0]
    return all(
        item.tool_name == first.tool_name and item.input_summary == first.input_summary
        for item in tail
    )


def budget_exhausted(state: "AuditState") -> str | None:
    """返回预算耗尽原因(超时/Token 预算),未耗尽返回 None(PRD §7.4)。"""
    control = state.get("control") or AgentControl()
    if control.timeout_seconds and elapsed_seconds(state) >= control.timeout_seconds:
        return f"任务超时（>{control.timeout_seconds:.0f}秒），已保存状态并停止"
    if control.token_budget and control.tokens_used >= control.token_budget:
        return f"Token 预算耗尽（{control.tokens_used}/{control.token_budget}），已停止模型调用"
    return None


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
    # 任务④/§15:困难区域与指令注入记录
    hard_regions: list[dict]
    injection_hits: list[dict]
    #: 发票专用 OCR 接口返回的结构化字段(项目字段名 → 规范化值),优先于正则抽取
    ocr_fields: dict[str, str]
    # §7.2:受控审核计划与自检结果
    audit_plan: Any
    critique: Any
    # 人工修正与运行时起点
    field_overrides: dict[str, dict]
    started_at: float
    #: 任务上下文(部门/任务日期),用于制度版本与部门过滤(FR-304)
    user_context: dict
