# OCR 与困难区域识别(任务④)

本文档说明材料解析路径、OCR 引擎选择,以及手写/困难区域识别与人工复核联动。

## 1. 处理路径(PRD §6.2)

| 材料类型 | 首选路径 | 回退路径 |
|---------|---------|---------|
| 电子 PDF(文本层充足) | 直接抽取文本 | — |
| 扫描 PDF(文本密度 < 阈值) | OCR | 引擎降级链 |
| 图片(PNG/JPG/JPEG) | OCR | 引擎降级链 |
| TXT/DOCX/XLSX | 直接解析 | — |

文本密度阈值由 `OCR_TEXT_DENSITY` 控制(默认 20 字符)。

## 2. 引擎与路由

`OCR_ENGINE` 选择引擎:

| 取值 | 引擎 | 说明 |
|------|------|------|
| `mock`(默认) | Mock | 确定性文本,离线可测(PRD §24.1) |
| `tesseract` | 本地 Tesseract | 需配置 `TESSERACT_EXE` 与中文语言包 |
| `http` | 企业 OCR 网关 | `OCR_HTTP_URL` + 可选密钥 |
| `mineru` | 本地 MinerU | 子进程调用,中文/复杂版式效果好,冷启动较慢 |
| `auto` | **难度路由**(任务④) | 困难/手写 → 高精度引擎;清晰 → 轻量引擎;失败逐级降级 |

路由链(`RoutingOcrEngine`):难点材料优先 MinerU → Tesseract → Mock,任一失败自动降级,主流程不中断(PRD §23.5)。

## 3. 困难区域评估(`app/parsers/region.py`)

两类评估,取更严重者:

**图片质量**(`assess_image`)
- 长边 < 800 px → 低分辨率
- 灰度标准差 < 15 → 低对比度(实测:单行文字≈24、灰字≈6、纯色=0)
- 图片无法解析 → 直接判困难

**文本质量**(`assess_text`)
- 识别文本 < 10 字符 → 困难
- 乱码率 > 30% → 困难
- 命中手写/盖章关键词(签字、签名、手写、盖章、印章、按手印、签署)→ **handwriting**

**输出**:`RegionAssessment(level, reasons, needs_human_review, suggested_engine)`,level ∈ `normal | hard | handwriting`。

## 4. 质量自适应识别(任务④核心)

Tesseract 引擎按图片质量决定策略顺序,并以 `text_quality_score`(乱码率 0.6 + 字段关键词命中 0.4)择优,达到 0.85 即提前返回:

| 材料 | 策略顺序 |
|------|---------|
| 清晰 | 原图 psm6 → 原图 psm3 → 预处理 psm6 |
| 困难 | 预处理 psm6 → 原图 psm6 → 原图 psm3 |

> 实测经验:预处理(灰度/放大/二值化)对**清晰小字有害**(会把"申请人"识别成 "FIBA"),对**低质/拍照材料有益**。
> 因此固定开启预处理反而降低准确率——必须质量自适应。修复后真实图片材料端到端从 5 字段/4 误报风险 提升到 **9 字段全抽 / PASS / 零风险**。

## 5. 人工复核联动

困难区域不修改 `Document` 契约,而是:
1. `parse_document` 给 `document_type` 追加难度后缀(`image_ocr_hard` / `image_ocr_handwriting`);
2. `parse_documents` 节点收集 `hard_regions`;
3. `check` 节点追加风险项与人工复核项:
   - 手写 → `HANDWRITING_REVIEW`
   - 低质/识别失败 → `OCR_QUALITY_REVIEW`

## 6. 环境准备

```bash
# 中文语言包(已随仓库提供 data/ocr_models/tessdata/chi_sim.traineddata)
# 若需重新下载(tessdata_fast, 约 2.4MB;GitHub 受限时用代理):
curl -sL -o data/ocr_models/tessdata/chi_sim.traineddata \
  https://ghproxy.net/https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata
```

`.env` 配置示例:

```ini
OCR_ENGINE=auto
TESSERACT_EXE=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_LANG=chi_sim+eng
TESSERACT_DATA_DIR=<仓库目录>\data\ocr_models\tessdata
OCR_PREPROCESS=1
```

## 7. 测试

```bash
python -m unittest tests.test_handwriting_region -v   # 30 项(含真实 Tesseract 集成,缺环境自动跳过)
```

覆盖:区域评估(分辨率/对比度/乱码/手写关键词)、预处理、质量评分、自适应策略顺序、路由降级、document_type 标记、端到端人工复核风险。
