"""OCR 引擎抽象与路由(PRD §6.2 / §8.1 / §23.5)。

设计原则:
- 默认 Mock 引擎:确定性、零依赖、离线可测(PRD §24.1「开发阶段默认开启Mock模式」);
- OCR_ENGINE=tesseract 时启用本机 Tesseract OCR,适合图片/截图类材料;
- OCR_ENGINE=http 时调用企业 OCR HTTP 服务,适合接百度/阿里/腾讯/自研 OCR 网关;
- OCR_ENGINE=mineru 时启用本机 MinerU 引擎,经子进程调用,输出并入统一文本流;
- 引擎可替换,业务代码只依赖 recognize() 接口(PRD §8.1「专用OCR/版面模型」)。

环境变量:
- OCR_ENGINE            mock(默认) | tesseract | http | mineru
- TESSERACT_EXE         tesseract 可执行文件路径(默认从 PATH 查找)
- TESSERACT_LANG        识别语言,默认 chi_sim+eng
- OCR_HTTP_URL          OCR HTTP 服务地址
- OCR_HTTP_API_KEY      OCR HTTP 服务密钥(可选)
- OCR_HTTP_TIMEOUT      OCR HTTP 超时秒数,默认 60
- OCR_HTTP_TEXT_PATH    返回 JSON 中文本字段路径,默认 data.text
- MINERU_EXE            mineru 可执行文件路径(Windows: .venv/Scripts/mineru.exe)
- MODELSCOPE_CACHE      模型缓存目录(缺省时使用 MINERU_MODELS 同目录 models/)
- MINERU_MODEL_SOURCE   模型源,默认 modelscope
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from app.config import load_environment

load_environment()

# PRD §6.2: 电子PDF文本密度过低时转扫描件路径的阈值(全页合计字符数)。
# 扫描件=0字符;演示发票≈40字符;真实电子PDF通常数百字符。默认20可容纳最短的有效文本。
DEFAULT_TEXT_DENSITY = int(os.environ.get("OCR_TEXT_DENSITY", "20"))


class OcrEngine:
    """OCR 引擎接口:输入文件字节,返回识别出的文本。"""

    name = "base"

    def recognize(self, content: bytes) -> str:
        raise NotImplementedError


class MockOcrEngine(OcrEngine):
    """确定性 Mock(PRD §24.1)。返回固定文本,用于验证路由与下游链路。

    文本刻意同时包含发票/付款/审批三类字段样例,便于 demo 端到端跑通。
    """

    name = "mock"
    MOCK_TEXT = (
        "【OCR-MOCK】发票号码：INV-MOCK-2026-001 发票金额：520.00元 开票日期：2026-05-12\n"
        "付款金额：520.00元 付款时间：2026-05-13 流水号：PAY-MOCK-001\n"
        "出差城市：杭州 出差开始：2026-05-10 出差结束：2026-05-12 申请人：张三\n"
    )

    def recognize(self, content: bytes) -> str:
        return self.MOCK_TEXT


class TesseractOcrEngine(OcrEngine):
    """本地 Tesseract OCR 引擎。

    适合 PNG/JPG/JPEG 等图片材料。扫描 PDF 更建议使用 MinerU 或 HTTP OCR,
    因为 Tesseract CLI 本身不负责 PDF 页面渲染。
    """

    name = "tesseract"

    def __init__(self, exe: str = "tesseract", lang: str = "chi_sim+eng") -> None:
        self.exe = exe
        self.lang = lang

    def recognize(self, content: bytes) -> str:
        resolved = shutil.which(self.exe) or (self.exe if Path(self.exe).exists() else None)
        if resolved is None:
            raise FileNotFoundError(
                "未找到 tesseract 可执行文件。请安装 Tesseract OCR, "
                "或设置 TESSERACT_EXE, 或改用 OCR_ENGINE=http。"
            )
        with tempfile.TemporaryDirectory(prefix="docaudit_tesseract_") as tmp:
            tmpdir = Path(tmp)
            source = tmpdir / "input.png"
            source.write_bytes(content)
            output_base = tmpdir / "ocr"
            proc = subprocess.run(
                [resolved, str(source), str(output_base), "-l", self.lang, "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("OCR_TIMEOUT_SECONDS", "120")),
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Tesseract OCR 失败: {proc.stderr[-500:]}")
            text_path = output_base.with_suffix(".txt")
            if not text_path.exists():
                raise RuntimeError("Tesseract OCR 未产出文本文件")
            text = text_path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                raise RuntimeError("Tesseract OCR 结果为空")
            return text


class HttpOcrEngine(OcrEngine):
    """企业 OCR HTTP 网关。

    请求体采用通用 JSON:
    {
      "file_base64": "...",
      "options": {"language": "zh,en"}
    }

    默认从返回 JSON 的 data.text 字段读取识别文本。不同供应商可通过
    OCR_HTTP_TEXT_PATH 调整字段路径,例如 result.text 或 text。
    """

    name = "http"

    def __init__(self, url: str, api_key: str | None = None, timeout_seconds: int = 60, text_path: str = "data.text") -> None:
        self.url = url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.text_path = text_path

    def recognize(self, content: bytes) -> str:
        if not self.url:
            raise RuntimeError("OCR_ENGINE=http 时必须配置 OCR_HTTP_URL")
        payload = {
            "file_base64": base64.b64encode(content).decode("ascii"),
            "options": {"language": os.environ.get("OCR_HTTP_LANGUAGE", "zh,en")},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OCR HTTP 服务调用失败: {exc}") from exc
        data = json.loads(body)
        text = _get_by_path(data, self.text_path)
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"OCR HTTP 响应中未找到文本字段: {self.text_path}")
        return text.strip()


class MineruOcrEngine(OcrEngine):
    """MinerU 引擎:子进程调用 mineru.exe,读取输出 markdown 为文本流。

    冷启动需要加载模型(CPU 数分钟),后续页复用进程;失败时上层降级(PRD §23.5)。
    """

    name = "mineru"

    def __init__(self, exe: str, cache_dir: str, model_source: str = "modelscope") -> None:
        self.exe = Path(exe)
        self.cache_dir = Path(cache_dir)
        self.model_source = model_source

    def recognize(self, content: bytes) -> str:
        if not self.exe.exists():
            raise FileNotFoundError(f"MINERU_EXE 不存在: {self.exe}")
        with tempfile.TemporaryDirectory(prefix="docaudit_ocr_") as tmp:
            tmpdir = Path(tmp)
            source = tmpdir / "input.pdf"
            source.write_bytes(content)
            out_dir = tmpdir / "out"
            env = os.environ.copy()
            env["MINERU_MODEL_SOURCE"] = self.model_source
            env["MODELSCOPE_CACHE"] = str(self.cache_dir)
            proc = subprocess.run(
                [str(self.exe), "-p", str(source), "-o", str(out_dir), "-b", "pipeline"],
                capture_output=True, text=True, timeout=3600,
                stdin=subprocess.DEVNULL, env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"MinerU 失败: {proc.stderr[-500:]}")
            md_files = sorted(out_dir.rglob("*.md"))
            if not md_files:
                raise RuntimeError("MinerU 未产出 markdown 输出")
            return md_files[0].read_text(encoding="utf-8")


def get_ocr_engine() -> OcrEngine:
    """工厂:按 OCR_ENGINE 返回 OCR 实例(PRD §8.2 模型路由)。"""
    mode = os.environ.get("OCR_ENGINE", "mock").strip().lower()
    if mode == "tesseract":
        return TesseractOcrEngine(
            exe=os.environ.get("TESSERACT_EXE", "tesseract"),
            lang=os.environ.get("TESSERACT_LANG", "chi_sim+eng"),
        )
    if mode == "http":
        return HttpOcrEngine(
            url=os.environ.get("OCR_HTTP_URL", ""),
            api_key=os.environ.get("OCR_HTTP_API_KEY"),
            timeout_seconds=int(os.environ.get("OCR_HTTP_TIMEOUT", "60")),
            text_path=os.environ.get("OCR_HTTP_TEXT_PATH", "data.text"),
        )
    if mode == "mineru":
        # 与 MinerU 已装环境一致的默认:脚本目录在 .venv/Scripts, 模型在同级 models/
        exe = os.environ.get("MINERU_EXE", r"<MinerU 环境>\.venv\Scripts\mineru.exe")
        cache = os.environ.get(
            "MODELSCOPE_CACHE",
            os.environ.get("MINERU_MODELS", r"<MinerU 环境>\models"),
        )
        return MineruOcrEngine(exe=exe, cache_dir=cache)
    return MockOcrEngine()


def _get_by_path(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
