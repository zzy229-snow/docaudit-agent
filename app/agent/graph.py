"""审核工作流入口(PRD §7.2/§7.3)。

引擎选择由环境变量 ``AGENT_ENGINE`` 控制:

- ``langgraph``(默认):用 ``app.agent.langgraph_workflow`` 的 StateGraph 执行,
  含 route_review 条件边;
- ``sequential``:顺序执行固定主流程(兜底实现,便于对照与排错);
- langgraph 不可用或构建失败时自动回退到 sequential,并在 trace 中记录原因。

两条路径共用同一套节点、状态与终止条件,因此结论一致(回归评测覆盖)。
"""
from __future__ import annotations

import os

from app.agent import pipeline
from app.agent.langgraph_workflow import LangGraphUnavailable, langgraph_available, run_with_langgraph
from app.models.audit import AuditReport

DEFAULT_ENGINE = "langgraph"


def resolve_engine() -> str:
    """解析当前应使用的引擎名。"""
    engine = os.environ.get("AGENT_ENGINE", DEFAULT_ENGINE).strip().lower() or DEFAULT_ENGINE
    return engine if engine in {"langgraph", "sequential"} else DEFAULT_ENGINE


def run_audit(
    files: list[tuple[str, bytes]],
    max_steps: int = 10,
    field_overrides: dict | None = None,
    task_id: str | None = None,
    department: str | None = None,
    as_of: str | None = None,
    llm_settings: dict | None = None,
) -> AuditReport:
    """执行一次完整审核。

    ``task_id`` 可传入稳定标识(如 API 任务 ID);不传则生成随机 ID。修正后重跑
    必须复用同一 task_id,否则会被重复发票查重误判为另一个任务(FR-204)。
    ``department``/``as_of`` 用于制度版本与部门过滤(FR-304)。
    ``llm_settings`` 是**运行时模型配置**(provider/base_url/model/api_key),来自界面
    "填空"或调用方;不传则用环境变量(向后兼容)。
    """
    state = pipeline.prepare_state(
        files, max_steps=max_steps, field_overrides=field_overrides, task_id=task_id,
        department=department, as_of=as_of, llm_settings=llm_settings,
    )
    engine = resolve_engine()
    if engine == "langgraph":
        try:
            if not langgraph_available():
                raise LangGraphUnavailable("未安装 langgraph")
            run_with_langgraph(state)
        except LangGraphUnavailable as exc:
            state["trace"] = [*state.get("trace", []), f"engine: langgraph 不可用（{exc}），回退顺序执行"]
            state["current_node"] = None
            pipeline.run_sequential(state)
    else:
        pipeline.run_sequential(state)
    return pipeline.finalize(state)
