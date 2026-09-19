"""审核流程的节点编排(PRD §7.1 固定主流程 + §7.4 终止条件)。

两种执行引擎共用同一套节点与状态:
- ``run_sequential``:顺序执行,作为无 LangGraph 依赖时的兜底(也是最小可读实现);
- ``app.agent.langgraph_workflow``:LangGraph ``StateGraph`` 版本,带条件边。

``wrap_node`` 负责统一的三件事:
1. 步数门控 ``require_next_step``(达到 ``max_steps`` 时安全终止);
2. 节点执行;
3. 合并"原地修改"的运行时字段(``tool_calls``/``agent_trace``/``human_review_items``/
   ``control``/``trace``),保证 LangGraph 的状态合并不会丢轨迹。
"""
from __future__ import annotations

from collections.abc import Callable

from app.models.audit import AuditReport
from app.models.state import AgentControl, new_task_id, require_next_step
from app.services.invoice_registry import register_task_invoice
from .nodes import check, critic, extract, parse_documents, plan, report, retrieve, route_review

#: 固定主流程节点顺序(与 PRD §7.2 节点表一一对应)
NODE_SEQUENCE: tuple[Callable[[dict], dict], ...] = (
    parse_documents,
    extract,
    retrieve,
    plan,
    check,
    critic,
    route_review,
    report,
)

#: 会原地修改 state 的运行时字段
_MUTATED_KEYS = ("tool_calls", "agent_trace", "human_review_items", "control", "trace")

NODE_NAMES: tuple[str, ...] = tuple(node.__name__ for node in NODE_SEQUENCE)


def wrap_node(node: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """给节点套上步数门控与状态合并。"""

    def runner(state: dict) -> dict:
        require_next_step(state, node.__name__)
        update = node(state) or {}
        merged = dict(update)
        for key in _MUTATED_KEYS:
            if key in state and key not in merged:
                merged[key] = state[key]
        return merged

    runner.__name__ = node.__name__
    return runner


def prepare_state(
    files: list[tuple[str, bytes]],
    max_steps: int | None = None,
    field_overrides: dict | None = None,
    task_id: str | None = None,
    timeout_seconds: float | None = None,
    department: str | None = None,
    as_of: str | None = None,
) -> dict:
    """构造初始审核状态(§7.3 AuditState)。

    ``department``/``as_of`` 进入 ``user_context``,供制度检索做部门与生效区间过滤(FR-304)。
    """
    from time import perf_counter

    if not files:
        raise ValueError("请上传至少一份材料")
    control_kwargs = {"max_steps": max_steps} if max_steps else {}
    if timeout_seconds:
        control_kwargs["timeout_seconds"] = timeout_seconds
    return {
        "task_id": task_id or new_task_id(),
        "files": files,
        "trace": [],
        "agent_trace": [],
        "tool_calls": [],
        "human_review_items": [],
        "field_overrides": field_overrides or {},
        "control": AgentControl(**control_kwargs),
        "started_at": perf_counter(),
        "user_context": {"department": department, "as_of": as_of},
    }


def finalize(state: dict) -> AuditReport:
    """收尾:登记发票查重记录(FR-204)并返回报告。"""
    report_value = state["report"]
    register_task_invoice(state["task_id"], state.get("fields") or {})
    return report_value


def run_sequential(state: dict) -> dict:
    """顺序执行固定主流程;节点抛错即中断(与 LangGraph 版一致)。"""
    for node in NODE_SEQUENCE:
        state["current_node"] = node.__name__
        state.update(wrap_node(node)(state))
    return state
