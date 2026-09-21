"""发票专用 OCR 接口连通性检查(拿到 key 后第一步跑这个)。

它做三件事:
1. 把一张发票图片发给配置好的 OCR 接口,打印**原始返回 JSON**(自动打码 key、截断 base64);
2. 按 OCR_HTTP_FIELDS_PATH / OCR_HTTP_FIELD_MAP 打印取到的结构化字段(规范化之后);
3. 用这台接口**真跑一遍完整审核**,打印结论状态、用上的字段和风险(结论不是"无法判定"
   就说明材料这次真的被读到了)。

用法(PowerShell / bash 均可,命令行参数优先于 .env):

    .\\.venv\\Scripts\\python.exe scripts\\check_ocr_http.py data\\demo\\invoice.png

    .\\.venv\\Scripts\\python.exe scripts\\check_ocr_http.py 我的发票.png \\
        --url https://ocr.example.com/v1/invoice \\
        --api-key sk-xxx \\
        --fields-path words_result \\
        --field-map '{"invoice_number":"InvoiceNum","invoice_date":"InvoiceDate","invoice_amount":"TotalAmount"}'

安全:接口 key 只从 .env 或命令行读,不回显、不落盘、不写进仓库。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_environment  # noqa: E402  (需在 sys.path 调整后导入)

MASK = "***已打码***"


def _shrink(value: object, limit: int = 400) -> object:
    """截断 base64 之类的大字段,便于阅读。"""
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}…(共 {len(value)} 字符)"
    if isinstance(value, dict):
        return {key: _shrink(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_shrink(item, limit) for item in value[:10]]
    return value


def _mask(text: str, secret: str | None) -> str:
    """把打印内容里出现的 key 打码(接口有时会回显)."""
    if secret and secret in text:
        return text.replace(secret, MASK)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="发票 OCR 接口连通性检查")
    parser.add_argument("image", help="发票图片路径(png/jpg/jpeg)")
    parser.add_argument("--provider", choices=["http", "baidu"],
                        help="接口类型:http(通用 JSON 网关)/ baidu(百度增值税发票识别);默认按 OCR_ENGINE")
    parser.add_argument("--url", help="OCR_HTTP_URL / BAIDU_OCR_URL 覆盖值")
    parser.add_argument("--api-key", help="API Key 覆盖值(只用于本次调用)")
    parser.add_argument("--secret-key", help="百度 Secret Key 覆盖值(只用于本次调用)")
    parser.add_argument("--text-path", help="OCR_HTTP_TEXT_PATH 覆盖值(默认 data.text)")
    parser.add_argument("--fields-path", help="OCR_HTTP_FIELDS_PATH 覆盖值(结构化字段所在对象路径)")
    parser.add_argument("--field-map", help='OCR_HTTP_FIELD_MAP 覆盖值,JSON: {"invoice_number":"InvoiceNum"}')
    args = parser.parse_args()

    load_environment()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"找不到图片:{image_path}")
        return 2
    content = image_path.read_bytes()

    provider = (args.provider or os.environ.get("OCR_ENGINE", "http")).strip().lower()
    if provider not in ("http", "baidu"):
        provider = "http"

    url = args.url or (os.environ.get("BAIDU_OCR_URL", "") if provider == "baidu"
                       else os.environ.get("OCR_HTTP_URL", ""))
    if not url and provider == "http":
        print("没有配置 OCR_HTTP_URL。请在 .env 里填,或用 --url 传一次性的接口地址。")
        return 2
    api_key = args.api_key or (os.environ.get("BAIDU_OCR_API_KEY") if provider == "baidu"
                               else os.environ.get("OCR_HTTP_API_KEY"))
    fields_path = (args.fields_path or os.environ.get("OCR_HTTP_FIELDS_PATH", "")).strip()
    field_map_raw = args.field_map or os.environ.get("OCR_HTTP_FIELD_MAP", "")
    text_path = args.text_path if args.text_path is not None else os.environ.get("OCR_HTTP_TEXT_PATH", "data.text")

    from app.parsers.ocr import (
        BAIDU_TOKEN_URL,
        BAIDU_VAT_URL,
        BaiduVatInvoiceOcrEngine,
        HttpOcrEngine,
        parse_field_map,
        parse_headers,
    )

    if provider == "baidu":
        engine = BaiduVatInvoiceOcrEngine(
            api_key=api_key or "",
            secret_key=args.secret_key or os.environ.get("BAIDU_OCR_SECRET_KEY", ""),
            url=url or BAIDU_VAT_URL,
            token_url=os.environ.get("BAIDU_OCR_TOKEN_URL", BAIDU_TOKEN_URL),
            timeout_seconds=int(os.environ.get("OCR_HTTP_TIMEOUT", "60")),
            field_map=parse_field_map(os.environ.get("BAIDU_OCR_FIELD_MAP", "")) or None,
            fields_path=fields_path or "words_result",
            text_path=text_path,
        )
    else:
        engine = HttpOcrEngine(
            url=url,
            api_key=api_key,
            timeout_seconds=int(os.environ.get("OCR_HTTP_TIMEOUT", "60")),
            text_path=text_path,
            fields_path=fields_path,
            field_map=parse_field_map(field_map_raw),
            extra_headers=parse_headers(os.environ.get("OCR_HTTP_HEADERS", "")),
        )

    print(f"接口类型: {provider}")
    print(f"接口: {engine.url}")
    print(f"图片: {image_path.name}({len(content)} 字节)")
    print(f"文本路径: {text_path or '(未配置,不取整段文本)'} | 字段路径: {engine.fields_path or '(未配置)'} | "
          f"字段映射: {len(engine.field_map)} 项 | key: {'已配置' if api_key else '未配置'}")
    print("-" * 70)

    print("[1/3] 调用接口…")
    try:
        raw = engine.call(content)
    except Exception as exc:  # noqa: BLE001 — 这是诊断脚本,原样报告
        print(f"调用失败: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(_shrink(raw), ensure_ascii=False, indent=2) if not api_key
          else _mask(json.dumps(_shrink(raw), ensure_ascii=False, indent=2), api_key))

    print("-" * 70)
    print("[2/3] 按映射取结构化字段:")
    fields = engine.extract_fields(raw)
    if not fields:
        print("  没取到任何字段。检查 OCR_HTTP_FIELDS_PATH / OCR_HTTP_FIELD_MAP 是否对得上上面的返回结构。")
    for name, value in fields.items():
        print(f"  {name:20} = {value}")

    print("-" * 70)
    print("[3/3] 用这台接口真跑一遍完整审核:")
    os.environ["OCR_ENGINE"] = provider
    os.environ["OCR_HTTP_FIELDS_PATH"] = fields_path
    if field_map_raw:
        os.environ["OCR_HTTP_FIELD_MAP"] = field_map_raw
    if args.api_key:
        os.environ["OCR_HTTP_API_KEY"] = args.api_key
    if args.secret_key:
        os.environ["BAIDU_OCR_SECRET_KEY"] = args.secret_key
    if provider == "baidu":
        os.environ["BAIDU_OCR_URL"] = url
        if args.api_key:
            os.environ["BAIDU_OCR_API_KEY"] = args.api_key
    else:
        os.environ["OCR_HTTP_URL"] = url

    from app.agent.graph import run_audit

    try:
        report = run_audit([(image_path.name, content)], task_id="task-ocr-check")
    except Exception as exc:  # noqa: BLE001
        print(f"审核执行失败: {type(exc).__name__}: {exc}")
        return 1

    print(f"  结论状态: {report.status}")
    if report.status == "UNDETERMINED":
        print(f"  ⚠️ 仍判为无法判定:{report.failure_reason}")
    print("  采用字段:")
    for name, field in report.fields.items():
        print(f"    {name:20} = {field.value!r:24} 证据={field.source_text[:46]!r}")
    print("  风险:")
    for risk in report.risks or []:
        print(f"    [{risk.level}] {risk.risk_type}: {risk.reason[:70]}")
    print("-" * 70)
    print("结论: ", "接口可用,材料被真正识别 ✅" if report.status != "UNDETERMINED"
          else "接口通了但材料仍未识别 ❌(看上面的字段映射与原始返回)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
