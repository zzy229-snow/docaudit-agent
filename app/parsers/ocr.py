"""OCR 引擎抽象与路由(PRD §6.2 / §8.1 / §23.5)。

设计原则:
- 默认 Mock 引擎:确定性、零依赖、离线可测(PRD §24.1「开发阶段默认开启Mock模式」);
- OCR_ENGINE=mineru 时启用本机 MinerU 引擎,经子进程调用,输出并入统一文本流;
- 引擎可替换,业务代码只依赖 recognize() 接口(PRD §8.1「专用OCR/版面模型」)。

环境变量:
- OCR_ENGINE            mock(默认) | mineru
- MINERU_EXE            mineru 可执行文件路径(Windows: .venv/Scripts/mineru.exe)
- MODELSCOPE_CACHE      模型缓存目录(缺省时使用 MINERU_MODELS 同目录 models/)
- MINERU_MODEL_SOURCE   模型源,默认 modelscope
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

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
    """工厂:按 OCR_ENGINE 返回 mock 或 mineru 实例(PRD §8.2 模型路由)。"""
    mode = os.environ.get("OCR_ENGINE", "mock").strip().lower()
    if mode == "mineru":
        # 与 MinerU 已装环境一致的默认:脚本目录在 .venv/Scripts, 模型在同级 models/
        exe = os.environ.get("MINERU_EXE", r"<MinerU 环境>\.venv\Scripts\mineru.exe")
        cache = os.environ.get(
            "MODELSCOPE_CACHE",
            os.environ.get("MINERU_MODELS", r"<MinerU 环境>\models"),
        )
        return MineruOcrEngine(exe=exe, cache_dir=cache)
    return MockOcrEngine()
