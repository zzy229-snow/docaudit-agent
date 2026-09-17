from app.models.audit import AuditReport
from app.models.state import AgentControl, new_task_id, require_next_step
from .nodes import parse_documents, extract, retrieve, check, report


def run_audit(files: list[tuple[str, bytes]], max_steps: int = 10, field_overrides: dict | None = None) -> AuditReport:
    """Small fixed workflow with a state interface ready for LangGraph integration."""
    if not files:
        raise ValueError("请上传至少一份材料")
    state: dict = {
        "task_id": new_task_id(),
        "files": files,
        "trace": [],
        "agent_trace": [],
        "tool_calls": [],
        "human_review_items": [],
        "field_overrides": field_overrides or {},
        "control": AgentControl(max_steps=max_steps),
    }
    for node in (parse_documents, extract, retrieve, check, report):
        state["current_node"] = node.__name__
        require_next_step(state, node.__name__)
        state.update(node(state))
    return state["report"]
