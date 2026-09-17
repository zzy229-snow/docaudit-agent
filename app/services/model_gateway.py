import json
import os
from typing import Any


class ModelGatewayError(RuntimeError):
    pass


class ModelGateway:
    """统一模型入口。默认 mock，不要求 API Key；真实模型走 OpenAI-compatible 协议。"""

    def __init__(self) -> None:
        self.provider = os.getenv("MODEL_PROVIDER", "mock").strip().lower()
        self.base_url = os.getenv("MODEL_BASE_URL", "").strip()
        self.model = os.getenv("MODEL_NAME", "").strip()
        self.api_key = os.getenv("MODEL_API_KEY", "").strip()
        self.timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "30"))

    def available(self) -> bool:
        if self.provider == "mock":
            return True
        return bool(self.base_url and self.model and self.api_key)

    def complete(self, instruction: str, content: str) -> str:
        if self.provider == "mock":
            return "{}"
        if not self.available():
            raise ModelGatewayError("请先配置MODEL_BASE_URL、MODEL_NAME和MODEL_API_KEY，或使用MODEL_PROVIDER=mock")
        try:
            from openai import OpenAI

            client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
            answer = client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": content},
                ],
            )
            return answer.choices[0].message.content or ""
        except Exception as exc:
            raise ModelGatewayError(f"模型调用失败: {exc}") from exc

    def complete_json(self, instruction: str, content: str) -> dict[str, Any]:
        raw = self.complete(instruction, content)
        return parse_json_object(raw)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ModelGatewayError("模型未返回JSON对象")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ModelGatewayError(f"模型JSON解析失败: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelGatewayError("模型JSON顶层必须是对象")
    return value
