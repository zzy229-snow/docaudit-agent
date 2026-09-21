# 发票 OCR 接入指南（OCR_ENGINE=http）

默认配置（`OCR_ENGINE=mock`）**不会真正识别图片/扫描件**：这类材料会被判为
`UNDETERMINED`（无法判定），而不是给出结论。要让系统真读发票，最省事的一条路是接
**发票专用 OCR 接口**（百度/阿里/腾讯等云厂商的"增值税发票识别"），因为这类接口本身就是
为票面训练的，号码/日期/价税合计直接以结构化字段返回，比通用 OCR + 版式正则准得多。

## 1. 两种响应模式

| 模式 | 接口返回 | 需要的配置 |
| --- | --- | --- |
| 文本模式 | 一整段识别文本 | `OCR_HTTP_TEXT_PATH`（默认 `data.text`） |
| 结构化模式（推荐） | 字段对象，如 `words_result.InvoiceNum` | `OCR_HTTP_FIELDS_PATH` + `OCR_HTTP_FIELD_MAP` |

两种可以同时配：先用结构化字段，若接口没有整段文本，系统会用字段拼一段可读文本，
保证质量评估、证据链和报告照常工作。

## 2. `.env` 配置

```dotenv
OCR_ENGINE=http
OCR_HTTP_URL=https://你的接口地址/v1/invoice
OCR_HTTP_API_KEY=你的key
OCR_HTTP_TIMEOUT=60

# 接口返回结构:字段所在对象路径(嵌套用点号,如 data.invoice)
OCR_HTTP_FIELDS_PATH=words_result

# 字段映射:左=本项目字段名,右=接口返回里的字段路径(相对上面那个对象)
OCR_HTTP_FIELD_MAP={"invoice_code":"InvoiceCode","invoice_number":"InvoiceNum","invoice_date":"InvoiceDate","invoice_amount":"TotalAmount","invoice_buyer":"BuyerName"}

# 接口若返回整段文本,顺手也配上(可选;不想用就留空字符串)
OCR_HTTP_TEXT_PATH=

# 需要额外请求头(如某些云厂商的签名头)时用 JSON 传(可选)
# OCR_HTTP_HEADERS={"X-Api-Key":"xxx","X-Signature":"xxx"}
```

请求体固定为通用 JSON（`file_base64` + `options.language`），`OCR_HTTP_API_KEY` 会以
`Authorization: Bearer <key>` 发送。若目标接口要求别的鉴权方式（如腾讯云 TC3 签名、
百度 access_token 走 query 参数），用 `OCR_HTTP_HEADERS` 补请求头，或告诉我们你的鉴权方式，
我们加一个适配分支。

### 可映射的本项目字段

| 字段名 | 用途 |
| --- | --- |
| `invoice_code` | 发票代码 |
| `invoice_number` | 发票号码（重复发票查重 FR-204 的依据） |
| `invoice_date` | 开票日期 |
| `invoice_amount` | 发票金额（与付款凭证核对） |
| `invoice_buyer` | 购买方（主体一致性） |
| `payment_amount` / `payment_party` | 付款金额 / 付款方（若接口也能识别付款凭证） |
| `travel_city` / `travel_start_date` / `travel_end_date` | 出差城市与行程日期（制度匹配与日期范围检查） |
| `applicant_name` | 申请人（主体一致性） |

取值会做规范化：`2019年02月19日` → `2019-02-19`；`¥1,280.00元` → `1280.00`；
`1234 5678` → `12345678`。嵌套/包装值（`{"value": "..."}`、`{"word": "..."}`）会自动取内层。

## 3. 接完之后怎么验（一条命令）

```powershell
.\.venv\Scripts\python.exe scripts\check_ocr_http.py 我的发票.png `
    --url https://你的接口地址/v1/invoice --api-key sk-xxx `
    --text-path "" --fields-path words_result `
    --field-map '{"invoice_number":"InvoiceNum","invoice_date":"InvoiceDate","invoice_amount":"TotalAmount"}'
```

脚本会打印 ①接口原始返回（key 自动打码、base64 截断）②按映射取到的字段（规范化后）
③**用这台接口真跑一遍完整审核**的结论。最后一行给出判断：

```text
结论:  接口可用,材料被真正识别 ✅
```

key 只从 `.env` 或命令行读，不回显、不落盘、不写进仓库（`.env` 已在 `.gitignore`）。

## 4. 字段优先级

```text
人工修正(field_overrides)  >  发票接口结构化字段(confidence 0.99)  >  LLM 抽取  >  版式正则
```

- 结构化字段与正则抽取**不一致**时采用接口值，并把冲突写进 trace：
  `ocr_structured_fields_conflict: 接口值与规则抽取不一致,已采用接口值 —— invoice_amount(接口=900.00 规则=111.00)`；
- 字段证据的 `source_text` 记为 `OCR结构化字段：invoice_amount=900.00`，能在报告「字段证据」里追溯到来源；
- 无字段可抽时仍按 §16 处理：不给合规结论。

## 5. 已知边界

- 本适配器不做云厂商签名算法（TC3/OSS 签名等），需要时用 `OCR_HTTP_HEADERS` 传已算好的头，
  或按你的厂商补一个适配分支；
- 接口本身的识别准确率不在本项目实现范围内，评测基线（`docs/EVALUATION.md`）只覆盖主流程逻辑；
- 材料类型（发票/付款凭证/审批单）目前按**文件名关键词**识别，文件名不含 `invoice`/`payment`/`approval`
  时会算作材料缺失 —— 上传时建议按规范命名，或告诉我们是否要按票面内容自动判类型。
