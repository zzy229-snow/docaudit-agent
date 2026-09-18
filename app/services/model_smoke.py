"""Model API readiness smoke check.

Run this before using a paid model API:

    python -m app.services.model_smoke

In mock mode it verifies that the project is still safely offline. In a real
OpenAI-compatible mode it performs one tiny JSON response call and validates
that the gateway can parse the response.
"""
from __future__ import annotations

import json

from app.services.model_gateway import ModelGateway, ModelGatewayError


def run_smoke_check(gateway: ModelGateway | None = None) -> dict[str, object]:
    gateway = gateway or ModelGateway()
    result: dict[str, object] = {
        "provider": gateway.provider,
        "available": gateway.available(),
        "model": gateway.model,
        "base_url_configured": bool(gateway.base_url),
    }
    if gateway.provider == "mock":
        result["status"] = "SKIPPED"
        result["message"] = "MODEL_PROVIDER=mock，当前处于离线安全模式，不会调用真实 API。"
        return result
    if not gateway.available():
        result["status"] = "FAILED"
        result["message"] = "真实模型配置不完整，请检查 MODEL_BASE_URL、MODEL_NAME、MODEL_API_KEY。"
        return result

    payload = gateway.complete_json(
        instruction=(
            "你是连通性检查器。只返回 JSON 对象，不要 Markdown，不要解释。"
        ),
        content='请返回 {"ok": true, "purpose": "model_smoke"}',
    )
    if payload.get("ok") is not True:
        raise ModelGatewayError(f"模型连通性检查返回异常: {payload}")
    result["status"] = "OK"
    result["message"] = "真实模型 API 可调用，且 JSON 解析正常。"
    result["response"] = payload
    return result


def main() -> None:
    try:
        result = run_smoke_check()
    except Exception as exc:  # noqa: BLE001 - CLI smoke check should print actionable diagnostics.
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
