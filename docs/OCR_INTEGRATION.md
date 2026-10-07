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

## 4.1 百度智能云「增值税发票识别」（现成配置）

百度走的是 `API Key + Secret Key → access_token → 识别接口`，请求体是
`application/x-www-form-urlencoded` 的 `image=<base64>`。这些差异已封装好，选
`OCR_ENGINE=baidu` 即可，token 会自动获取、缓存、失效重试。

```dotenv
OCR_ENGINE=baidu
BAIDU_OCR_API_KEY=你的API Key          # 百度控制台「应用列表」里的 API Key
BAIDU_OCR_SECRET_KEY=你的Secret Key    # 同一位置的「应用密钥」(32 位),两个都要
# 下面这些是默认值,一般不用改
# BAIDU_OCR_URL=https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice
# BAIDU_OCR_TOKEN_URL=https://aip.baidubce.com/oauth/2.0/token
```

默认字段映射（对应 `words_result` 里的键）：

| 本项目字段 | 百度字段 | 说明 |
| --- | --- | --- |
| `invoice_code` | `InvoiceCode` | 发票代码 |
| `invoice_number` | `InvoiceNum` | 发票号码（重复发票查重依据） |
| `invoice_date` | `InvoiceDate` | 开票日期（`2016年06月02日` → `2016-06-02`） |
| `invoice_amount` | `AmountInFiguers` | **价税合计（小写）**，与付款凭证金额同口径，用于金额一致性核对 |
| `invoice_buyer` | `PurchaserName` | 购买方 |

刻意**不映射**票面的 `Province`/`City` → `travel_city`：票面地址是**销售方所在地**，
不等于出差城市，拿去匹配住宿标准会得出错误结论；出差城市仍由审批单/行程单提供。
若你要的是不含税金额（`TotalAmount`）或税额（`TotalTax`），用 `BAIDU_OCR_FIELD_MAP`
覆盖即可。

验证（补齐 Secret Key 后一条命令）：

```powershell
.\.venv\Scripts\python.exe scripts\check_ocr_http.py 我的发票.png --provider baidu
```

## 4.2 本地 MinerU（免费、离线、不把发票外发）

企业报销场景里很多客户**不允许发票出内网**，这条路就是给他们的：MinerU 本地版面解析，
免费、离线，识别质量实测远超 Tesseract（后者对同一张测试票**一个字段都抽不出来**）。

MinerU 是**可选**的本地引擎（免费、离线、不把发票外发）。先自己装好 MinerU 并下载模型，再指过去：

```dotenv
OCR_ENGINE=mineru
# 默认值就是下面这两条,机器上路径不同才需要写
# MINERU_EXE=<你的 venv>/Scripts/mineru.exe
# MODELSCOPE_CACHE=<模型缓存目录>
MINERU_BACKEND=pipeline
MINERU_API_URL=http://127.0.0.1:8321
```

**先起常驻服务再跑**（脚本已备好）：

```powershell
scripts\start_mineru_api.bat          # 内部执行 mineru-api --host 127.0.0.1 --port 8321
```

| 方式 | 单张耗时（CPU，实测 2026-10-01） | 说明 |
| --- | --- | --- |
| 常驻 `mineru-api` + `MINERU_API_URL` | **第 1 张 28.2 s / 第 2 张 18.5 s** | 模型只加载一次，推荐 |
| 不配 `MINERU_API_URL` | **约 115 s** | CLI 每次自起临时服务并重新加载模型 |

若常驻服务没起来，引擎会**自动退回**"不用 `--api-url`"（慢但照样出结论，不会让任务失败）。

### MinerU 输出为什么要归一化

MinerU 吐的是 Markdown + HTML 表格，标签会切断"标签—值"的相邻关系。实测原始输出：

```html
<tr><td rowspan=1 colspan=2>价税合计(大写)</td><td rowspan=1 colspan=9>玖佰元整 (小写)¥900.00</td></tr>
```

直接喂规则抽取器会**漏抽金额**（标签里还夹着 `colspan=9` 这种数字）。因此引擎内部统一走
`normalize_mineru_text()`：丢图片占位 → 表格单元格逐格换行 → 去标签 → 还原 HTML 实体
（发票密码区全是 `&lt;`/`&gt;`）→ 去 Markdown 标题符号。

同时修掉两个真实票面问题：

| 问题 | 原因 | 处理 |
| --- | --- | --- |
| `payment_party` 抽成 `"复核"` | 票面底部签章栏是"收款人：/复核:/开票人：前台"，被当成收款方 | 整行出现 ≥2 个签章标签即丢弃；值命中签章用词也丢弃 |
| 日期存成 `2019年02月19日` | 规则抽取原样取值 | 日期字段统一规范化为 `2019-02-19` |
| 购买方抽不到 | 真票"购买方"与"名 称：<公司>"分格换行 | 增加"名 称：<公司名>"写法，用负向断言在"纳税人识别号"处截断 |

同一张测试发票在两条路线上的对照（字段口径一致，够用）：

| 字段 | MinerU（本地·免费） | 百度（云端·付费） |
| --- | --- | --- |
| 发票代码 / 号码 | ✅ 110022003300 / 12345678 | ✅ 同 |
| 开票日期 | ✅ 2019-02-19（规范化后） | ✅ 2019-02-19 |
| 价税合计 | ✅ 900.00 | ✅ 900.00 |
| 购买方 | ✅ 示例科技有限公司 | ✅ 同 |
| 耗时 | 18~28 s（常驻） | 1.2 s |
| 数据是否外发 | **否** | **是** |

## 5. 已知边界

- 本适配器不做云厂商签名算法（TC3/OSS 签名等），需要时用 `OCR_HTTP_HEADERS` 传已算好的头，
  或按你的厂商补一个适配分支（百度已内置，见 4.1）；
- 接口本身的识别准确率不在本项目实现范围内，评测基线（`docs/EVALUATION.md`）只覆盖主流程逻辑；
- 材料类型默认**按票面内容判定**（发票代码/号码、价税合计、增值税、付款金额、流水号、审批意见等），
  内容判不出来（例如一页混合了发票和付款信息、或纯白纸）才回退文件名关键词；两类都判不出时
  按 `other` 处理 —— 宁可提示人工确认，也不假装材料齐全。阈值可用
  `MATERIAL_MIN_CONTENT_SIGNALS`（默认 2）调整。
