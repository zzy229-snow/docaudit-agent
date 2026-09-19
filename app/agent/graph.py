from app.models.audit import AuditReport
from app.models.state import AgentControl, new_task_id, require_next_step
from app.services.invoice_registry import register_task_invoice
from .nodes import parse_documents, extract, retrieve, check, report


def run_audit(
    files: list[tuple[str, bytes]],
    max_steps: int = 10,
    field_overrides: dict | None = None,
    task_id: str | None = None,
) -> AuditReport:
    """Small fixed workflow with a state interface ready for LangGraph integration.

    ``task_id`` 可传入稳定标识(如 API 任务 ID);不传则生成随机 ID。修正后重跑
    必须复用同一 task_id,否则会被重复发票查重误判为另一个任务(FR-204)。
    """
    if not files:
        raise ValueError("请上传至少一份材料")
    state: dict = {
        "task_id": task_id or new_task_id(),
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
    # FR-204:任务完成后登记发票关键字段,供后续任务查重
    register_task_invoice(state["task_id"], state["fields"])
    return state["report"]
