"""OCR 引擎抽象与路由(PRD §6.2 / §8.1 / §23.5)。

设计原则:
- 默认 Mock 引擎:确定性、零依赖、离线可测(PRD §24.1「开发阶段默认开启Mock模式」);
- OCR_ENGINE=tesseract 时启用本机 Tesseract OCR(支持自定义语言包目录,含中文);
- OCR_ENGINE=http 时调用企业 OCR HTTP 服务,适合接百度/阿里/腾讯/自研 OCR 网关;
- OCR_ENGINE=mineru 时启用本机 MinerU 引擎,经子进程调用,输出并入统一文本流;
- OCR_ENGINE=auto 时按材料难度自动路由(任务④):困难/手写 → 高精度引擎,普通 → 轻量引擎;
- 引擎可替换,业务代码只依赖 recognize() 接口(PRD §8.1「专用OCR/版面模型」)。

任务④ 手写/困难区域识别:
- 图片预处理(灰度/放大/自动对比度/二值化)提升低质与手写材料识别率;
- 困难区域评估见 app/parsers/region.py,评估结果决定引擎路由与人工复核标记。

环境变量:
- OCR_ENGINE            mock(默认) | tesseract | http | mineru | auto | stub
- TESSERACT_EXE         tesseract 可执行文件路径(默认从 PATH 查找)
- TESSERACT_LANG        识别语言,默认 chi_sim+eng
- TESSERACT_DATA_DIR    语言包目录(含 chi_sim.traineddata;默认项目 data/ocr_models/tessdata)
- OCR_PREPROCESS        图片预处理开关,默认 1(0 关闭)
- OCR_HTTP_URL          OCR HTTP 服务地址
- OCR_HTTP_API_KEY      OCR HTTP 服务密钥(可选)
- OCR_HTTP_TIMEOUT      OCR HTTP 超时秒数,默认 60
- OCR_HTTP_TEXT_PATH    返回 JSON 中文本字段路径,默认 data.text(置空则不取文本)
- OCR_HTTP_FIELDS_PATH  返回 JSON 中结构化字段所在对象路径,如 words_result / data.invoice
- OCR_HTTP_FIELD_MAP    字段映射 JSON,如 {"invoice_number":"InvoiceNum","invoice_amount":"TotalAmount"}
- OCR_HTTP_HEADERS      额外请求头 JSON(如腾讯云签名头),与 Bearer key 并存
- MINERU_EXE            mineru 可执行文件路径(Windows: .venv/Scripts/mineru.exe)
- MODELSCOPE_CACHE      模型缓存目录(缺省时使用 MINERU_MODELS 同目录 models/)
- MINERU_MODEL_SOURCE   模型源,默认 modelscope
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

from app.config import load_environment

load_environment()

# PRD §6.2: 电子PDF文本密度过低时转扫描件路径的阈值(全页合计字符数)。
# 扫描件=0字符;演示发票≈40字符;真实电子PDF通常数百字符。默认20可容纳最短的有效文本。
DEFAULT_TEXT_DENSITY = int(os.environ.get("OCR_TEXT_DENSITY", "20"))

# 项目自带语言包目录(chi_sim + eng),避免污染系统 Tesseract 安装
DEFAULT_TESSDATA_DIR = Path(__file__).resolve().parents[2] / "data" / "ocr_models" / "tessdata"

#: 百度智能云 增值税发票识别:接口地址与 access_token 地址
BAIDU_VAT_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"

#: 百度发票识别默认字段映射(接口字段 → 本项目字段)。
#: 价税合计取 AmountInFiguers(小写),与付款凭证金额同口径;票面 Province/City 不映射出差城市
#: —— 票面地址是销售方所在地,不等于出差城市,拿来匹配住宿标准会得出错误结论。
BAIDU_VAT_FIELD_MAP: dict[str, str] = {
    "invoice_code": "InvoiceCode",
    "invoice_number": "InvoiceNum",
    "invoice_date": "InvoiceDate",
    "invoice_amount": "AmountInFiguers",
    "invoice_buyer": "PurchaserName",
}


def preprocess_image_for_ocr(content: bytes, scale: int = 2) -> bytes:
    """图片预处理:灰度 → 放大 → 自动对比度 → 均值二值化(任务④)。

    低分辨率/低对比度材料(常见于手写、拍照上传)经预处理后识别率明显提升。
    失败时原样返回,保证主流程不中断。
    """
    try:
        from PIL import Image, ImageOps
        with Image.open(BytesIO(content)) as img:
            gray = img.convert("L")
            width, height = gray.size
            if scale > 1:
                gray = gray.resize((width * scale, height * scale), Image.LANCZOS)
            gray = ImageOps.autocontrast(gray)
            hist = gray.histogram()
            total = sum(hist) or 1
            mean = sum(i * count for i, count in enumerate(hist)) / total
            gray = gray.point(lambda p: 255 if p > mean else 0)
            buffer = BytesIO()
            gray.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 — 预处理失败退回原图
        return content


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


class StubOcrEngine(MockOcrEngine):
    """合成语料引擎(仅评测/演示):文本不带演示标记,按"已识别"对待。

    与 ``mock`` 的唯一区别:mock 表示"没接真实 OCR,材料未被识别",会被材料可读性
    判定拦下(``app.services.readability``)并判为"无法判定";stub 明确声明喂进去的
    是合成语料(离线评测/回归用),不代表真实 OCR 能力。
    """

    name = "stub"
    MOCK_TEXT = (
        "发票号码：INV-MOCK-2026-001 发票金额：520.00元 开票日期：2026-05-12\n"
        "付款金额：520.00元 付款时间：2026-05-13 流水号：PAY-MOCK-001\n"
        "出差城市：杭州 出差开始：2026-05-10 出差结束：2026-05-12 申请人：张三\n"
    )


class TesseractOcrEngine(OcrEngine):
    """本地 Tesseract OCR 引擎(任务④:中文语言包 + 质量自适应多策略)。

    策略(以文本质量评分择优,质量达标即提前返回):
    - 清晰材料:原图优先(预处理的二值化会损害清晰小字),不足再试其他 psm / 预处理;
    - 困难材料(低分辨率/低对比度/拍照):预处理优先,再回退原图;
    - 适合 PNG/JPG/JPEG 图片;扫描 PDF 更建议 MinerU 或 HTTP OCR。
    """

    name = "tesseract"

    def __init__(self, exe: str = "tesseract", lang: str = "chi_sim+eng",
                 tessdata_dir: str | None = None, preprocess: bool = True,
                 adaptive: bool = True, min_quality: float = 0.85) -> None:
        self.exe = exe
        self.lang = lang
        self.tessdata_dir = tessdata_dir
        self.preprocess = preprocess
        self.adaptive = adaptive
        self.min_quality = min_quality

    def _resolve_exe(self) -> str:
        resolved = shutil.which(self.exe) or (self.exe if Path(self.exe).exists() else None)
        if resolved is None:
            raise FileNotFoundError(
                "未找到 tesseract 可执行文件。请安装 Tesseract OCR, "
                "或设置 TESSERACT_EXE, 或改用 OCR_ENGINE=http。"
            )
        return resolved

    def _run_once(self, payload: bytes, psm: int) -> str:
        resolved = self._resolve_exe()
        with tempfile.TemporaryDirectory(prefix="docaudit_tesseract_") as tmp:
            tmpdir = Path(tmp)
            source = tmpdir / "input.png"
            source.write_bytes(payload)
            output_base = tmpdir / "ocr"
            env = os.environ.copy()
            if self.tessdata_dir:
                env["TESSDATA_PREFIX"] = str(self.tessdata_dir)
            proc = subprocess.run(
                [resolved, str(source), str(output_base), "-l", self.lang, "--psm", str(psm)],
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("OCR_TIMEOUT_SECONDS", "120")),
                stdin=subprocess.DEVNULL,
                env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Tesseract OCR 失败: {proc.stderr[-500:]}")
            text_path = output_base.with_suffix(".txt")
            if not text_path.exists():
                return ""
            return text_path.read_text(encoding="utf-8", errors="ignore").strip()

    def _strategies(self, content: bytes) -> list[tuple[bytes, int]]:
        """按图片质量决定策略顺序(任务④:质量自适应)。"""
        if not self.adaptive or not self.preprocess:
            return [(content, 6)]
        from app.parsers.region import assess_image
        assessment = assess_image(content)
        if assessment.is_hard:
            # 困难材料:预处理优先,再回退原图与另一 psm
            return [(preprocess_image_for_ocr(content), 6), (content, 6), (content, 3)]
        # 清晰材料:原图优先(预处理会损害清晰小字),不足再试其他策略
        return [(content, 6), (content, 3), (preprocess_image_for_ocr(content), 6)]

    def recognize(self, content: bytes) -> str:
        from app.parsers.region import text_quality_score

        best_text, best_score = "", -1.0
        errors: list[str] = []
        for payload, psm in self._strategies(content):
            try:
                text = self._run_once(payload, psm)
            except (RuntimeError, FileNotFoundError) as exc:
                errors.append(str(exc))
                if isinstance(exc, FileNotFoundError):
                    raise
                continue
            score = text_quality_score(text)
            if score > best_score:
                best_text, best_score = text, score
            if score >= self.min_quality:
                break
        if not best_text.strip():
            detail = "; ".join(errors) if errors else "所有策略均无有效文本"
            raise RuntimeError(f"Tesseract OCR 结果为空: {detail}")
        return best_text


class HttpOcrEngine(OcrEngine):
    """企业 OCR HTTP 网关。

    请求体采用通用 JSON:
    {
      "file_base64": "...",
      "options": {"language": "zh,en"}
    }

    文本模式:从返回 JSON 的 ``text_path`` 读取整段识别文本(默认 ``data.text``,
    可用 OCR_HTTP_TEXT_PATH 调整,如 ``result.text``;置空则不取文本字段)。

    结构化字段模式:发票专用 OCR 接口通常直接返回结构化字段而不是整段文本。
    配置 ``fields_path``(字段所在对象路径,如 ``words_result``)与 ``field_map``
    (项目字段名 → 该对象内的字段路径)后,引擎会取值并规范化(日期 ISO、金额去符号、
    号码去分隔符),通过 ``last_fields`` 交给上层,优先于正则抽取;若接口没有整段
    文本,会用这些字段拼一段可读文本,保证下游质量评估与证据链正常。
    """

    name = "http"

    def __init__(self, url: str, api_key: str | None = None, timeout_seconds: int = 60,
                 text_path: str = "data.text", fields_path: str = "",
                 field_map: dict[str, str] | None = None,
                 extra_headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.text_path = text_path
        self.fields_path = fields_path
        self.field_map = dict(field_map or {})
        self.extra_headers = dict(extra_headers or {})
        #: 最近一次调用取到的结构化字段(项目字段名 → 规范化后的值)
        self.last_fields: dict[str, str] = {}

    def recognize(self, content: bytes) -> str:
        if not self.url:
            raise RuntimeError("OCR_ENGINE=http 时必须配置 OCR_HTTP_URL")
        data = self.call(content)
        fields = self.extract_fields(data)
        text = ""
        if self.text_path:
            raw = _get_by_path(data, self.text_path)
            if isinstance(raw, str):
                text = raw.strip()
        if not text and fields:
            # 结构化接口没有整段文本:用字段拼一段可读文本,保证下游质量评估/证据链可用
            text = text_from_fields(fields)
        if not text:
            raise RuntimeError(
                "OCR HTTP 响应中未找到文本字段"
                f"({self.text_path or '未配置'})，也未按 OCR_HTTP_FIELDS_PATH/OCR_HTTP_FIELD_MAP"
                f" 取到结构化字段({self.fields_path or '未配置'})"
            )
        self.last_fields = fields
        return text

    def call(self, content: bytes) -> object:
        """调用一次 HTTP 接口并返回解析后的 JSON(重试/诊断脚本复用)。"""
        if not self.url:
            raise RuntimeError("OCR_ENGINE=http 时必须配置 OCR_HTTP_URL")
        payload = {
            "file_base64": base64.b64encode(content).decode("ascii"),
            "options": {"language": os.environ.get("OCR_HTTP_LANGUAGE", "zh,en")},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
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
        return json.loads(body)

    def extract_fields(self, data: object) -> dict[str, str]:
        """按 OCR_HTTP_FIELDS_PATH + OCR_HTTP_FIELD_MAP 取出并规范化结构化字段。"""
        if not self.fields_path or not self.field_map:
            return {}
        container = _get_by_path(data, self.fields_path)
        if not isinstance(container, dict):
            return {}
        fields: dict[str, str] = {}
        for name, path in self.field_map.items():
            value = normalize_ocr_value(name, _get_by_path(container, path))
            if value:
                fields[name] = value
        return fields


class BaiduVatInvoiceOcrEngine(HttpOcrEngine):
    """百度智能云「增值税发票识别」(OCR_ENGINE=baidu)。

    与通用 HttpOcrEngine 的差别:
    - 鉴权:先用 API Key + Secret Key 换 access_token(缓存复用,过期或 token 失效时自动刷新),
      再以 access_token 作为查询参数调用识别接口;
    - 请求:``application/x-www-form-urlencoded``,字段名 ``image=<base64>``;
    - 响应:``words_result`` 里的结构化字段,按默认映射规范化成本项目字段
      (价税合计 ``AmountInFiguers`` → ``invoice_amount``,与付款金额同口径)。

    刻意**不**把发票上的 Province/City 映射成 ``travel_city``:票面地址是销售方所在地,
    不等于出差城市,拿来匹配住宿标准会得出错误结论。
    """

    name = "baidu"

    def __init__(self, api_key: str = "", secret_key: str = "", url: str = "",
                 token_url: str = "", timeout_seconds: int = 60,
                 field_map: dict[str, str] | None = None,
                 fields_path: str = "words_result", text_path: str = "") -> None:
        super().__init__(url=url or BAIDU_VAT_URL, api_key=None, timeout_seconds=timeout_seconds,
                         text_path=text_path, fields_path=fields_path,
                         field_map=dict(field_map or BAIDU_VAT_FIELD_MAP))
        self.api_key = api_key
        self.secret_key = secret_key
        self.token_url = token_url or BAIDU_TOKEN_URL
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        #: 诊断用:token 换了几次
        self.token_refreshes = 0

    def access_token(self, force: bool = False) -> str:
        """获取(并缓存)access_token;``force=True`` 时强制刷新。"""
        if self._access_token and not force and time.time() < self._token_expires_at:
            return self._access_token
        if not self.api_key or not self.secret_key:
            raise RuntimeError(
                "OCR_ENGINE=baidu 需要 API Key 与 Secret Key:"
                "请配置 BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY"
                "(或 OCR_HTTP_API_KEY / OCR_HTTP_API_KEY_SECRET)")
        query = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        })
        request = urllib.request.Request(f"{self.token_url}?{query}", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"百度 access_token 获取失败: {exc}") from exc
        token = data.get("access_token")
        if not token:
            raise RuntimeError(
                "百度 access_token 获取失败:"
                f"{data.get('error', 'unknown')}/{data.get('error_description', '请检查 API Key 与 Secret Key')}")
        self._access_token = str(token)
        self._token_expires_at = time.time() + max(60, int(data.get("expires_in", 2592000)) - 300)
        self.token_refreshes += 1
        return self._access_token

    def call(self, content: bytes) -> object:
        body = urllib.parse.urlencode({"image": base64.b64encode(content).decode("ascii")}).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        headers.update(self.extra_headers)
        for attempt in range(2):
            token = self.access_token(force=attempt > 0)
            request = urllib.request.Request(
                f"{self.url}?access_token={token}", data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.URLError as exc:
                raise RuntimeError(f"百度发票识别调用失败: {exc}") from exc
            code = data.get("error_code") if isinstance(data, dict) else None
            if code in (110, 111) and attempt == 0:
                continue     # token 无效/过期:刷新后重试一次
            if code:
                raise RuntimeError(f"百度发票识别返回错误 {code}: {data.get('error_msg')}")
            return data
        raise RuntimeError("百度发票识别调用失败:access_token 刷新后仍被拒绝")


class MineruOcrEngine(OcrEngine):
    """MinerU 引擎:子进程调用 mineru.exe,并把输出归一化成可抽取文本。

    两个实测要点(2026-10-01,CPU、单张发票):

    1. **速度**:不指定 ``--api-url`` 时,mineru CLI 每次调用都会自起一个临时服务并
       **重新加载模型**,单张图约 115 秒。要提速应先起常驻服务
       ``mineru-api --host 127.0.0.1 --port 8321``,再设 ``MINERU_API_URL`` 指过去,
       模型只加载一次,后续每张只剩推理时间。
    2. **输出形态**:MinerU 产出 Markdown + HTML 表格,标签会打断"标签—值"的相邻关系
       (例如 ``价税合计(大写)</td><td ...>玖佰元整 (小写)¥900.00``),直接喂给规则抽取器
       会漏抽。因此这里统一走 :func:`normalize_mineru_text`:表格逐格换行、去标签、
       还原 HTML 实体。
    """

    name = "mineru"

    def __init__(self, exe: str, cache_dir: str, model_source: str = "modelscope",
                 backend: str = "pipeline", api_url: str = "") -> None:
        #: 未配置时为 None —— 不能写成 Path("")(那会等于当前目录,exists() 为真)
        self.exe = Path(exe) if exe else None
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.model_source = model_source
        self.backend = backend
        #: 常驻 mineru-api 地址(为空则每次自起临时服务,很慢)
        self.api_url = api_url

    def available(self) -> bool:
        return self.exe is not None and self.exe.exists()

    def resolved_api_url(self) -> str:
        """常驻服务不可达时返回空串(不传 ``--api-url``)。

        为什么要判断:``--api-url`` 指向一个没起来的服务会让 CLI 直接失败、审核变 FAILED;
        而退回"不传"只是慢(CLI 自起临时服务),仍能出结论。宁可慢,不可挂。
        """
        if not self.api_url:
            return ""
        parsed = urllib.parse.urlparse(self.api_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return self.api_url
        except OSError:
            return ""

    def recognize(self, content: bytes) -> str:
        if self.exe is None:
            raise FileNotFoundError(
                "未配置 MINERU_EXE:请把它指向你本机的 mineru 可执行文件"
                "(如 <你的venv>\\Scripts\\mineru.exe),或改用 OCR_ENGINE=tesseract/http。"
            )
        if not self.exe.exists():
            raise FileNotFoundError(f"MINERU_EXE 指向的文件不存在: {self.exe}")
        with tempfile.TemporaryDirectory(prefix="docaudit_ocr_") as tmp:
            tmpdir = Path(tmp)
            suffix = ".png" if content[:8].startswith(b"\x89PNG") else ".pdf"
            source = tmpdir / f"input{suffix}"
            source.write_bytes(content)
            out_dir = tmpdir / "out"
            env = os.environ.copy()
            env["MINERU_MODEL_SOURCE"] = self.model_source
            if self.cache_dir is not None:
                env["MODELSCOPE_CACHE"] = str(self.cache_dir)
            command = [str(self.exe), "-p", str(source), "-o", str(out_dir), "-b", self.backend]
            api_url = self.resolved_api_url()
            if api_url:
                command += ["--api-url", api_url]
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=3600,
                stdin=subprocess.DEVNULL, env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"MinerU 失败: {proc.stderr[-500:]}")
            md_files = sorted(out_dir.rglob("*.md"))
            if not md_files:
                raise RuntimeError("MinerU 未产出 markdown 输出")
            return normalize_mineru_text(md_files[0].read_text(encoding="utf-8"))


class RoutingOcrEngine(OcrEngine):
    """按材料难度自动路由(任务④)。

    - 困难/手写材料(低分辨率、低对比度、图片不可解析)→ 高精度引擎(默认 MinerU);
    - 普通材料 → 轻量引擎(默认 Tesseract);
    - 所选引擎不可用或调用失败 → 依次降级,最终回退到 Mock(PRD §23.5 不中断主流程)。

    last_assessment 保存最近一次的评估结果,供上层标记人工复核。
    """

    name = "auto"

    def __init__(self, light: OcrEngine, high: OcrEngine, fallback: OcrEngine | None = None) -> None:
        self.light = light
        self.high = high
        self.fallback = fallback or MockOcrEngine()
        self.last_assessment = None

    def recognize(self, content: bytes) -> str:
        from app.parsers.region import assess_image

        assessment = assess_image(content)
        self.last_assessment = assessment
        candidates = [self.high, self.light] if assessment.is_hard else [self.light, self.high]
        candidates.append(self.fallback)
        errors = []
        for engine in candidates:
            available = getattr(engine, "available", None)
            if callable(available) and not available():
                errors.append(f"{engine.name}: 不可用")
                continue
            try:
                return engine.recognize(content)
            except Exception as exc:  # noqa: BLE001 — 逐个降级
                errors.append(f"{engine.name}: {exc}")
        raise RuntimeError("所有 OCR 引擎均失败: " + "; ".join(errors))


def get_ocr_engine() -> OcrEngine:
    """工厂:按 OCR_ENGINE 返回 OCR 实例(PRD §8.2 模型路由)。"""
    mode = os.environ.get("OCR_ENGINE", "mock").strip().lower()
    if mode == "baidu":
        # 百度智能云 增值税发票识别:API Key + Secret Key 换 token,结构化字段返回
        return BaiduVatInvoiceOcrEngine(
            api_key=os.environ.get("BAIDU_OCR_API_KEY") or os.environ.get("OCR_HTTP_API_KEY", ""),
            secret_key=(os.environ.get("BAIDU_OCR_SECRET_KEY")
                        or os.environ.get("OCR_HTTP_API_KEY_SECRET", "")
                        or os.environ.get("OCR_HTTP_SECRET_KEY", "")),
            url=os.environ.get("BAIDU_OCR_URL", BAIDU_VAT_URL),
            token_url=os.environ.get("BAIDU_OCR_TOKEN_URL", BAIDU_TOKEN_URL),
            timeout_seconds=int(os.environ.get("OCR_HTTP_TIMEOUT", "60")),
            field_map=parse_field_map(os.environ.get("BAIDU_OCR_FIELD_MAP", "")) or None,
            fields_path=os.environ.get("OCR_HTTP_FIELDS_PATH", "words_result").strip() or "words_result",
            text_path=os.environ.get("OCR_HTTP_TEXT_PATH", ""),
        )
    if mode == "stub":
        # 合成语料(仅评测/演示):等价 mock 文本,但被视为"已识别"
        return StubOcrEngine()
    if mode == "tesseract":
        return TesseractOcrEngine(
            exe=os.environ.get("TESSERACT_EXE", "tesseract"),
            lang=os.environ.get("TESSERACT_LANG", "chi_sim+eng"),
            tessdata_dir=os.environ.get("TESSERACT_DATA_DIR", str(DEFAULT_TESSDATA_DIR)),
            preprocess=os.environ.get("OCR_PREPROCESS", "1").strip() != "0",
        )
    if mode == "http":
        return HttpOcrEngine(
            url=os.environ.get("OCR_HTTP_URL", ""),
            api_key=os.environ.get("OCR_HTTP_API_KEY"),
            timeout_seconds=int(os.environ.get("OCR_HTTP_TIMEOUT", "60")),
            text_path=os.environ.get("OCR_HTTP_TEXT_PATH", "data.text"),
            fields_path=os.environ.get("OCR_HTTP_FIELDS_PATH", "").strip(),
            field_map=parse_field_map(os.environ.get("OCR_HTTP_FIELD_MAP", "")),
            extra_headers=parse_headers(os.environ.get("OCR_HTTP_HEADERS", "")),
        )
    if mode == "mineru":
        return _build_mineru_engine()
    if mode == "auto":
        high = _build_mineru_engine()
        light_exe = os.environ.get("TESSERACT_EXE", "tesseract")
        light = TesseractOcrEngine(
            exe=light_exe,
            lang=os.environ.get("TESSERACT_LANG", "chi_sim+eng"),
            tessdata_dir=os.environ.get("TESSERACT_DATA_DIR", str(DEFAULT_TESSDATA_DIR)),
            preprocess=os.environ.get("OCR_PREPROCESS", "1").strip() != "0",
        )
        return RoutingOcrEngine(light=light, high=high, fallback=MockOcrEngine())
    return MockOcrEngine()


def _build_mineru_engine() -> MineruOcrEngine:
    # 需要用户显式配置(开源版不带任何本机默认路径):
    #   MINERU_EXE        mineru 可执行文件(Windows 一般在 <venv>/Scripts/mineru.exe)
    #   MODELSCOPE_CACHE  / MINERU_MODELS  模型缓存目录
    exe = os.environ.get("MINERU_EXE", "").strip()
    cache = os.environ.get(
        "MODELSCOPE_CACHE",
        os.environ.get("MINERU_MODELS", "").strip(),
    )
    return MineruOcrEngine(
        exe=exe,
        cache_dir=cache,
        backend=os.environ.get("MINERU_BACKEND", "pipeline"),
        # 常驻 mineru-api 地址:设了才复用已加载的模型(否则每次自起临时服务,约 100 秒/张)
        api_url=os.environ.get("MINERU_API_URL", "").strip(),
    )


def _get_by_path(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


# ---------------- MinerU 输出归一化 ----------------

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s*")
_HTML_ENTITIES: tuple[tuple[str, str], ...] = (
    ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
    ("&nbsp;", " "), ("&amp;", "&"),      # &amp; 放最后,避免二次解码
)


def normalize_mineru_text(markdown: str) -> str:
    """MinerU 的 Markdown/HTML 输出 → 规则抽取器能读的纯文本。

    为什么必须做:实测 MinerU 把这行票面内容输出成
    ``价税合计(大写)</td><td rowspan=1 colspan=9>玖佰元整 (小写)¥900.00</td>``,
    标签(还含数字 ``colspan=9``)会把"标签—值"切断,导致金额抽不出来。归一化后
    每个单元格各占一行,标签与值重新相邻。

    处理:丢图片占位 → 表格单元格/行切行 → 去标签 → 还原 HTML 实体(发票密码区
    全是 ``&lt;`` ``&gt;``)→ 去掉 Markdown 标题符号 → 逐行折叠空白、丢空行。
    """
    if not markdown:
        return ""
    text = _MD_IMAGE_RE.sub(" ", markdown)
    text = re.sub(r"</t[dh]\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub(" ", text)
    for entity, char in _HTML_ENTITIES:
        text = text.replace(entity, char)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(_MARKDOWN_HEADING_RE.sub("", line).split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


# ---------------- 结构化字段(发票专用 OCR 接口) ----------------

#: 结构化字段名 → 拼文本时用的中文标签(与规则抽取器的标签保持一致)
FIELD_TEXT_LABELS: dict[str, str] = {
    "invoice_code": "发票代码",
    "invoice_number": "发票号码",
    "invoice_date": "开票日期",
    "invoice_amount": "发票金额",
    "payment_amount": "付款金额",
    "invoice_buyer": "购买方",
    "payment_party": "付款方",
    "travel_city": "出差城市",
    "travel_start_date": "出差开始",
    "travel_end_date": "出差结束",
    "applicant_name": "申请人",
}

_AMOUNT_RE = re.compile(r"-?\d+(?:\.\d{1,2})?")
_DATE_PARTS_RE = re.compile(r"(\d{4})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})")


def normalize_ocr_value(field_name: str, raw: object) -> str:
    """把 OCR 接口返回的原始值规范化成本项目的字段值。

    - 日期类:``2019年02月19日`` / ``2019/2/19`` / ``2019-02-19 00:00:00`` → ``2019-02-19``
    - 金额类:``¥900.00`` / ``￥1,280.00元`` / ``900`` → ``900.00`` / ``1280.00``
    - 号码类:去掉空格与分隔符(``1234 5678`` → ``12345678``)
    - 其余:折叠空白(字符串直接保留)
    - 接口把字段包成 ``{"value": ...}`` / ``{"text": ...}`` / ``{"word": ...}`` 时自动取内层值
    """
    if raw is None:
        return ""
    if isinstance(raw, dict):
        for key in ("value", "text", "word", "content"):
            if raw.get(key) not in (None, ""):
                return normalize_ocr_value(field_name, raw.get(key))
        return ""
    if isinstance(raw, (list, tuple)):
        return " ".join(part for part in (normalize_ocr_value(field_name, item) for item in raw) if part)
    text = str(raw).strip()
    if not text:
        return ""
    key = field_name.lower()
    if "date" in key:
        return _normalize_date(text)
    if "amount" in key:
        match = _AMOUNT_RE.search(text.replace(",", ""))
        return f"{float(match.group()):.2f}" if match else ""
    if "number" in key or "code" in key:
        return re.sub(r"[^0-9A-Za-z]", "", text)
    return " ".join(text.split())


def _normalize_date(text: str) -> str:
    match = _DATE_PARTS_RE.search(text)
    if not match:
        return " ".join(text.split())
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def text_from_fields(fields: dict[str, str]) -> str:
    """结构化字段 → 可读文本(接口不返回整段文本时的兜底)。"""
    parts = [f"{FIELD_TEXT_LABELS.get(name, name)}：{value}"
             for name, value in fields.items() if value]
    return ("OCR结构化字段 " + " ".join(parts)).strip()


def parse_field_map(raw: str | None) -> dict[str, str]:
    """解析 OCR_HTTP_FIELD_MAP(JSON:项目字段名 → 接口字段路径)。"""
    if not raw or not raw.strip():
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OCR_HTTP_FIELD_MAP 不是合法 JSON: {exc}") from exc
    if not isinstance(mapping, dict):
        raise RuntimeError("OCR_HTTP_FIELD_MAP 必须是 JSON 对象,如 {\"invoice_number\":\"InvoiceNum\"}")
    return {str(name): str(path) for name, path in mapping.items()}


def parse_headers(raw: str | None) -> dict[str, str]:
    """解析 OCR_HTTP_HEADERS(JSON:请求头)。"""
    if not raw or not raw.strip():
        return {}
    try:
        headers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OCR_HTTP_HEADERS 不是合法 JSON: {exc}") from exc
    if not isinstance(headers, dict):
        raise RuntimeError("OCR_HTTP_HEADERS 必须是 JSON 对象,如 {\"X-Api-Key\":\"...\"}")
    return {str(name): str(value) for name, value in headers.items()}
