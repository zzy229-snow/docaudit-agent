"""LangGraph 版审核工作流(PRD §7.2 节点与条件边 / §7.3 状态)。

节点集合与 ``app.agent.pipeline.NODE_SEQUENCE`` 完全相同:
    parse_documents → extract → retrieve → plan → check → critic
    → route_review ─┬→（continue / review / fail）report → END

``route_review`` 用条件边决定走向:三种结论都收敛到 ``report`` 节点,
最终状态由 ``control.route`` 决定(continue→PASS/REVIEW_REQUIRED,fail→FAILED),
因此与顺序执行版结论完全一致,便于回归对比。

依赖:``langgraph>=0.2,<2``(requirements.txt)。未安装时由 ``app.agent.graph``
回退到顺序执行版,并在 trace 里说明,避免流程不可用。
"""
from __future__ import annotations

from app.agent.pipeline import wrap_node
from app.models.state import AuditState
from .nodes import check, critic, extract, parse_documents, plan, report, retrieve, route_review


class LangGraphUnavailable(RuntimeError):
    """langgraph 未安装或版本不兼容。"""


def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def build_audit_graph():
    """构建 StateGraph(节点 + 固定边 + route_review 条件边)。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # noqa: BLE001
        raise LangGraphUnavailable(f"langgraph 不可用:{exc}") from exc

    builder = StateGraph(AuditState)
    builder.add_node("parse_documents", wrap_node(parse_documents))
    builder.add_node("extract", wrap_node(extract))
    builder.add_node("retrieve", wrap_node(retrieve))
    builder.add_node("plan", wrap_node(plan))
    builder.add_node("check", wrap_node(check))
    builder.add_node("critic", wrap_node(critic))
    builder.add_node("route_review", wrap_node(route_review))
    builder.add_node("report", wrap_node(report))

    builder.add_edge(START, "parse_documents")
    builder.add_edge("parse_documents", "extract")
    builder.add_edge("extract", "retrieve")
    builder.add_edge("retrieve", "plan")
    builder.add_edge("plan", "check")
    builder.add_edge("check", "critic")
    builder.add_edge("critic", "route_review")
    # 条件边:continue/review/fail 三种路由都进入 report,由 control.route 决定状态
    builder.add_conditional_edges(
        "route_review",
        _route_selector,
        {"continue": "report", "review": "report", "fail": "report"},
    )
    builder.add_edge("report", END)
    return builder.compile()


def _route_selector(state: dict) -> str:
    control = state.get("control")
    route = getattr(control, "route", None) or "continue"
    return route if route in {"continue", "review", "fail"} else "continue"


def run_with_langgraph(state: dict) -> dict:
    """用 LangGraph 执行;返回合并后的最终状态。"""
    graph = build_audit_graph()
    final_state = graph.invoke(state)
    # LangGraph 返回的可能是新 dict;把原地字段回填给调用方持有的 state
    for key, value in final_state.items():
        state[key] = value
    return state
