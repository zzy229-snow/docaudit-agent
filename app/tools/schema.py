from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.audit import CheckResult


ToolCategory = Literal["document", "amount", "date", "policy", "database"]
ToolDeterminism = Literal["deterministic", "model_assisted"]


class ToolInputSchema(BaseModel):
    name: str
    description: str
    required: bool = True


class ToolSpec(BaseModel):
    name: str
    description: str
    category: ToolCategory
    inputs: list[ToolInputSchema]
    deterministic: ToolDeterminism = "deterministic"
    idempotent: bool = True
    timeout_ms: int = Field(default=3000, ge=1)


class ToolExecution(BaseModel):
    spec: ToolSpec
    input_summary: dict[str, Any]
    result: CheckResult


ToolHandler = Callable[..., CheckResult]


class RegisteredTool(BaseModel):
    spec: ToolSpec
    handler: ToolHandler

    model_config = {"arbitrary_types_allowed": True}

    def execute(self, **kwargs: Any) -> ToolExecution:
        result = self.handler(**kwargs)
        return ToolExecution(spec=self.spec, input_summary=_summarize_inputs(kwargs), result=result)


def _summarize_inputs(kwargs: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, list):
            summary[key] = f"list[{len(value)}]"
        elif isinstance(value, dict):
            summary[key] = f"dict[{len(value)}]"
        else:
            summary[key] = value
    return summary
