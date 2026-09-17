"""Optional API adapter; the baseline workflow does not require an API key."""
import os


class ModelGateway:
    def __init__(self):
        self.base_url = os.getenv("MODEL_BASE_URL", "")
        self.model = os.getenv("MODEL_NAME", "")
        self.api_key = os.getenv("MODEL_API_KEY", "")

    def available(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def complete(self, instruction: str, content: str) -> str:
        if not self.available():
            raise RuntimeError("请先配置MODEL_BASE_URL、MODEL_NAME和MODEL_API_KEY")
        from openai import OpenAI
        client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=30)
        answer = client.chat.completions.create(model=self.model, temperature=0,
            messages=[{"role": "system", "content": instruction}, {"role": "user", "content": content}])
        return answer.choices[0].message.content or ""
