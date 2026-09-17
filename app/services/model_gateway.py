"""OpenAI 兼容 LLM 适配器。

两条路径:
- complete(): 普通文本补全(原接口,向后兼容)。
- complete_json(): 强制 tool_choice 的 function calling,返回结构化 JSON。
  DeepSeek 官方 / 硅基流动均不支持 json_schema 模式的 structured output,
  统一走 function calling(记忆:with_structured_output 须 method='function_calling')。

任一路径失败(网络/解析/服务端错误)均抛 RuntimeError,由调用方决定降级策略。
"""

import json
import os

from openai import OpenAI


class ModelGateway:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 30.0):
        self.base_url = base_url if base_url is not None else os.getenv("MODEL_BASE_URL", "")
        self.model = model if model is not None else os.getenv("MODEL_NAME", "deepseek-chat")
        self.api_key = api_key if api_key is not None else os.getenv("MODEL_API_KEY", "")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def complete(self, instruction: str, content: str) -> str:
        if not self.available():
            raise RuntimeError("请先配置MODEL_BASE_URL、MODEL_NAME和MODEL_API_KEY")
        client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        answer = client.chat.completions.create(model=self.model, temperature=0,
            messages=[{"role": "system", "content": instruction}, {"role": "user", "content": content}])
        return answer.choices[0].message.content or ""

    def complete_json(self, instruction: str, content: str, schema: dict,
                      tool_name: str = "structured_output") -> dict:
        """强制 function calling 输出符合 schema 的 JSON 对象。

        schema 为 function parameters 的 JSON Schema(properties 逐字段描述)。
        """
        if not self.available():
            raise RuntimeError("请先配置MODEL_BASE_URL、MODEL_NAME和MODEL_API_KEY")
        client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": "将抽取结果按给定 schema 输出为 JSON",
                "parameters": schema,
            },
        }
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": content},
        ]
        last_error: Exception | None = None
        for _ in range(2):  # 解析/调用失败重试一次
            try:
                answer = client.chat.completions.create(model=self.model, temperature=0,
                    messages=messages,
                    tools=[tool],
                    tool_choice={"type": "function", "function": {"name": tool_name}})
                msg = answer.choices[0].message
                if not msg.tool_calls:
                    raise RuntimeError("模型未返回 tool_call,无法解析结构化输出")
                arguments = msg.tool_calls[0].function.arguments
                parsed = json.loads(arguments)
                if not isinstance(parsed, dict):
                    raise RuntimeError(f"tool_call arguments 不是 JSON 对象: {arguments[:200]}")
                return parsed
            except (json.JSONDecodeError, RuntimeError, ValueError, TypeError) as exc:
                last_error = exc
                # 追加纠错提示后重试
                messages = messages + [{
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "call_retry", "type": "function",
                                    "function": {"name": tool_name, "arguments": "{}"}}],
                }, {"role": "tool", "tool_call_id": "call_retry",
                    "content": f"解析失败: {exc}. 请只输出合法的 JSON。"}]
        raise RuntimeError(f"结构化输出失败: {last_error}")


def default_gateway() -> ModelGateway:
    """按环境变量构造网关;未配置时 available() 返回 False(调用方走降级)。"""
    return ModelGateway()
