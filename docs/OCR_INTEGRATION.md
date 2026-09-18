# 真实 OCR 接入说明

当前系统已提供统一 OCR 网关，业务流程只依赖 `OcrEngine.recognize()`，可以按部署条件切换不同真实 OCR 引擎。

## 1. OCR 模式

| 模式 | 配置 | 适用场景 | 是否真实 OCR |
| --- | --- | --- | --- |
| Mock | `OCR_ENGINE=mock` | 本地单测、离线演示 | 否 |
| Tesseract | `OCR_ENGINE=tesseract` | 本机图片 OCR、低成本开发验证 | 是 |
| HTTP | `OCR_ENGINE=http` | 百度/阿里/腾讯/自研 OCR 服务 | 是 |
| MinerU | `OCR_ENGINE=mineru` | 扫描 PDF、版面解析、复杂文档 | 是 |

生产化演示不要使用 `mock`。如果没有采购云 OCR，建议先用 Tesseract 做本地真实 OCR；如果要展示企业级能力，建议接 HTTP OCR 服务。

## 2. Tesseract 本地 OCR

安装 Tesseract 后，在 `.env` 中配置：

```env
OCR_ENGINE=tesseract
TESSERACT_EXE=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_LANG=chi_sim+eng
```

适合识别 PNG/JPG/JPEG 图片材料。扫描 PDF 更建议用 MinerU 或 HTTP OCR 服务，因为 Tesseract CLI 本身不负责复杂 PDF 页面渲染。

## 3. HTTP OCR 服务

`.env` 示例：

```env
OCR_ENGINE=http
OCR_HTTP_URL=https://ocr.example.com/v1/recognize
OCR_HTTP_API_KEY=replace-with-your-ocr-key
OCR_HTTP_TEXT_PATH=data.text
OCR_HTTP_TIMEOUT=60
```

系统发送 JSON 请求：

```json
{
  "file_base64": "...",
  "options": {
    "language": "zh,en"
  }
}
```

默认从响应 JSON 的 `data.text` 读取识别文本。如果供应商返回格式不同，可以通过 `OCR_HTTP_TEXT_PATH` 调整，例如：

```env
OCR_HTTP_TEXT_PATH=result.text
```

## 4. MinerU

如果本机已经安装 MinerU：

```env
OCR_ENGINE=mineru
MINERU_EXE=D:\path\to\mineru.exe
MODELSCOPE_CACHE=D:\path\to\modelscope-cache
```

MinerU 更适合扫描 PDF 和复杂版面材料，但首次加载模型较慢，对机器环境要求更高。

## 5. 验证步骤

先运行 OCR 路由测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_ocr_route -v
```

再运行全量测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

最后打开 Streamlit 工作台，上传图片或扫描件，确认任务事件、字段证据和风险结果是否符合预期。

## 6. 生产化边界

真实 OCR 接入后，还需要继续补：

- OCR 结果置信度；
- 表格/版面坐标；
- 发票专用结构化字段；
- OCR 失败重试和人工补录；
- 多页 PDF 分页 OCR；
- OCR 服务限流、超时和费用监控。
