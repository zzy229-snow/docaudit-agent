# DocAudit Agent

企业报销材料审核项目的稳定演示版。上传电子PDF、TXT、DOCX、XLSX、图片材料，系统会解析文本或走 OCR 路由，提取明确标注的字段，检索演示制度并运行五项确定性检查。默认不需要GPU或模型API；生产化演示应将 OCR、LLM 和 RAG 从 mock 模式切到真实服务。

当前稳定演示版适合演示、评审和本地联调；不应描述为生产级财务系统。

## 运行环境

- Python 3.11或3.12
- Windows、macOS或Linux

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

一键启动（含依赖安装与就绪检查）：`bash scripts/start.sh`（Windows：`scripts\start.bat`），
停止：`bash scripts/stop.sh`。Docker Compose 单机部署：`docker compose up -d --build`
（API 8000 / 工作台 8500），详见 `docs/DEPLOYMENT.md`。

打开页面后，分别上传 `data/demo/normal/` 下的三份TXT，可得到 `PASS`；上传 `data/demo/over_limit/` 下的三份TXT，可得到住宿超标80元、发票购买方与申请人不一致和制度引用。文件名应包含 `invoice`、`payment`、`approval`，作为当前材料类型识别依据。请勿将真实敏感材料上传到公开部署页面。

Streamlit 页面已升级为审核工作台形态：左侧可创建 5 组虚构样例任务，主界面包含任务中心、新建审核、审核详情、风险看板、字段证据、人工复核、事件时间线、HTML 正式报告和 JSON 审核包下载。

## FastAPI接口

```bash
python -m uvicorn app.api.main:app --reload --port 8102
```

接口文档地址：`http://127.0.0.1:8102/docs`。当前API默认使用本地 SQLite 任务存储，数据落在 `data/runtime/audit_tasks.sqlite3`，支持创建任务、上传材料、运行审核、任务列表、生命周期事件、字段修正、人工复核决策，以及查询字段、风险、执行轨迹和可解释摘要。详细说明见 `docs/API.md`。

## 已完成能力

- 六项确定性审核：必备材料、金额一致性、日期范围、住宿标准、主体一致性、重复发票查重。
- 材料可读性判定：材料没被真正识别（演示引擎文本、解析为空、抽不到任何字段）时**不给结论**，任务状态为 `UNDETERMINED`（无法判定），只保留一条 `MATERIAL_UNREADABLE` 风险并说明改法 —— 避免"上传 A 发票、结论却是 B 发票"的误导。结论状态：`PASS` / `REVIEW_REQUIRED` / `UNDETERMINED` / `FAILED`。
- **本地离线识别（`OCR_ENGINE=mineru`）**：复用本机 MinerU（免费、离线，发票不出内网），实测同一张测试票识别质量与云端发票接口口径一致（代码/号码/日期/价税合计/购买方），常驻服务下单张 18~28 秒（不起常驻则约 115 秒）；引擎内置输出归一化（表格逐格换行、去标签、还 HTML 实体）并修掉签章栏误抽主体、日期未规范化、购买方抽不到三个真实票面问题。参见 `docs/OCR_INTEGRATION.md` §4.2。
- **模型 API 界面填空**：交付时不留内置密钥 —— 侧栏「模型 API 设置（填空）」选供应商、填客户自己的 API Key，`测试连接` 当场验证（失败原样回显模型返回原因），`保存到本次会话` 立即生效；配置只存浏览器会话（不落盘、不改 `.env`），且只影响工作台进程（`/api/v1` 仍读服务端 `.env`）。实现走 `run_audit(..., llm_settings=...)` 参数而非进程环境变量。参见 `docs/MODEL_GATEWAY.md` §5。
- 发票 OCR 接入：`OCR_ENGINE=http` 支持两种接口形态 —— 返回整段文本（`OCR_HTTP_TEXT_PATH`）或**直接返回结构化字段**（`OCR_HTTP_FIELDS_PATH` + `OCR_HTTP_FIELD_MAP`，发票专用接口推荐），结构化字段优先级高于版式正则、冲突写进 trace；百度智能云增值税发票识别内置为 `OCR_ENGINE=baidu`（token 自动换发/缓存/失效重试）；`scripts/check_ocr_http.py` 可一条命令验证接口（详见 `docs/OCR_INTEGRATION.md`）。
- 材料类型按票面内容判定：发票/付款凭证/审批单靠票面信号识别（发票号码、价税合计、付款金额、流水号、审批意见…），文件名只在内容判不出时兜底 —— 上传「微信图片_2026.png」也不会被算成缺少发票。
- 风险等级口径：`AMOUNT_MATCH`（金额不一致，HIGH）与 `AMOUNT_UNVERIFIABLE`（金额字段缺失、无法核对，MEDIUM）分开，缺字段不再被算成"金额不符"。
- 演示模式提示：`OCR_ENGINE=mock` / `MODEL_PROVIDER=mock` 时，工作台顶部与新建审核页会明确标出"当前不识别真实材料"，不再让人误以为系统读错了。
- 报销单命名：展示名按 **日期区间 + 人物 + 事件** 自动生成（如 `2026.9.18-9.19 张三 住宿报销`），技术编号 `task-xxx` 退居次要；新建审核可填备注/事由作为“事件”，运行审核后按材料自动补全日期，支持改名且改名后不被覆盖；备注里已写的日期/人物不会重复拼接。
- 风险四级分级：HIGH/MEDIUM/LOW/INFO，HIGH 必须人工复核、LOW/INFO 仅提示（PRD 附录A）。
- 制度无依据不判合规：检索不到适用条款时输出“缺少规则依据，需人工确认”（PRD §16 / AC-05）。
- 指令注入防护：材料正文命中“忽略规则/直接判合规/跳过检查”等话术时记录 `PROMPT_INJECTION`（HIGH），但不改写任何确定性结论（PRD §15）。
- 重复发票查重：按发票号码+开票日期+金额查询本地登记表，命中返回关联任务编号；登记表路径由 `INVOICE_REGISTRY_PATH` 控制（默认 `data/runtime/invoice_registry.sqlite3`）。
- 文本材料质量检测：TXT/DOCX/XLSX 正文含签字/手写/盖章关键词或乱码率过高时标记困难材料并转人工（PRD §6.4）。
- 可解释报告：风险原因、字段证据、制度依据、检查项状态、Agent trace。
- Streamlit 演示页：内置 5 组虚构样例，支持 JSON 报告下载。
- FastAPI 任务接口：创建任务、上传材料、运行审核、字段修正、人工复核决策。
- 生产化任务基础：SQLite 持久化任务、材料、报告、人工修正、复核项和审计事件。
- 报告导出：支持通过 API 和 Streamlit 下载 HTML 正式审核报告。
- 制度管理接口：上传制度（TXT/DOCX/PDF）→ 自动切片入库 → 发布/停用/版本切换，检索按部门与生效区间过滤（FR-301~FR-304）。
- 最小可用 RBAC：`AUTH_MODE=enforce` 后按角色（申请人/审核员/制度管理员/管理员/开发测试）与任务归属鉴权，越权 404 不泄露存在性并写审计事件（§2/§15/AC-09）。
- 部署交付：Docker Compose 单机部署 + 一键启动脚本（`scripts/start.sh`、`scripts/start.bat`）。
- 离线评测接口：`POST /api/v1/evaluations/run` 直接返回回归指标与逐例结果（可输出 Markdown）。
- LLM 接入护栏：JSON Schema 校验、原文证据校验、格式标准化、低置信拒收、重试和规则兜底。
- 评测基线：57 条端到端样例（含重复发票/制度无依据/指令注入/OCR困难/手写关键词），输出 JSON/Markdown 报告，并统计风险 P/R/F1、工具成功率、人工复核率与 P50/P95 耗时。

## 模型API

默认使用 `MODEL_PROVIDER=mock`，不需要API Key。真实模型接入通过 `app/services/model_gateway.py` 统一管理，字段抽取Prompt位于 `app/prompts/field_extraction_v1.txt`。LLM 字段抽取已加入 JSON Schema 校验、原文证据校验、格式标准化、低置信拒收、重试和规则兜底。配置说明见 `docs/MODEL_GATEWAY.md`，稳定性护栏说明见 `docs/LLM_ROBUSTNESS.md`。

购买真实API前，先复制 `.env.example` 为 `.env`。项目会自动读取仓库根目录下的 `.env`，但 `.env` 已被 `.gitignore` 排除，不要提交密钥。然后运行连通性检查：

```bash
python -m app.services.model_smoke
```

真实模型 API 购买和接入步骤见 `docs/MODEL_API_READINESS.md`。

## 真实 OCR

默认 `OCR_ENGINE=mock` 只适合本地测试。生产化演示可以切换为：

- `OCR_ENGINE=tesseract`：本机 Tesseract OCR；
- `OCR_ENGINE=http`：百度/阿里/腾讯/自研 OCR HTTP 服务；
- `OCR_ENGINE=mineru`：扫描 PDF 和复杂版面解析。

配置方式见 `docs/OCR_INTEGRATION.md`。

## 真实 RAG

默认 `RAG_MODE=mock` 适合离线测试。生产化演示建议切换到 `RAG_MODE=local`，系统会从 `data/policies` 的制度切片中进行本地真实检索；如果已经准备好 bge-m3 和 Milvus Lite，也可以使用 `RAG_MODE=milvus`。配置和评测方式见 `docs/RAG_INTEGRATION.md`。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试**不读 `.env`**：本机 ``.env`` 里可以配真实 OCR（如 `OCR_ENGINE=baidu`）或真实模型，
测试仍全程离线、可复现。两个原因值得知道：一是 `app.config.load_environment` 在 unittest
下会跳过 `.env`（`AUDIT_LOAD_DOTENV=1` 可强制读）；二是 `pymilvus` 这类第三方库在 import 时
会自己 `load_dotenv()`，而 venv 位于仓库内时它会找到仓库 `.env`，所以引擎敏感的测试模块会在
导入前显式钉住 `OCR_ENGINE=mock`。评测同理，默认强制 `OCR_ENGINE=stub` 合成语料引擎
（想用真引擎跑评测设 `EVAL_OCR_ENGINE=baidu`）。

测试**不碰生产数据**：任务库、发票查重登记表、已发布制度库在单测进程里全部指向临时目录
（闸门在 `app/api/store.py` 的 `_default_db_path()`，与"单测不读 `.env`"同一口径）。
为什么闸门必须装在应用侧：`unittest discover -s tests` 把测试模块当**顶层模块**导入
（`test_xxx` 而不是 `tests.test_xxx`），`tests/__init__.py` 不会先执行 —— 只在包初始化里设
环境变量是无效的。没这道闸门时，**一轮单测会往生产任务库写 28 条演示报销单**，
工作台任务列表里会凭空多出"张三/alice 住宿报销"。`tests/test_runtime_isolation.py` 锁住了这条规则。

## 评测基线

```bash
python -m app.evaluation.runner
python -m app.evaluation.runner --format markdown
python -m app.evaluation.runner --format markdown --output reports/eval-report.md
```

评测用例位于 `data/evaluation/cases.json`，会端到端检查最终状态、风险码、关键字段和制度引用。后续接入真实OCR、LLM或向量检索后，先跑这组评测确认准确率和稳定性没有下降。详细说明见 `docs/EVALUATION.md`。

## 目录

`app/models` 是统一数据结构；`app/parsers` 解析文件；`app/extraction` 抽字段；`app/rag` 读取和匹配演示制度；`app/tools` 精确校验；`app/agent` 串联审核流程；`app/services` 预留模型API接口；`app/evaluation` 提供端到端评测；`data` 为虚构样例和预期结果。

## 当前限制

此版是可解释的演示基线。默认 OCR、RAG、LLM 均可在 mock/offline 模式下运行；任务和审核结果已支持本地 SQLite 持久化。OCR 已提供 Tesseract、HTTP OCR、MinerU 三类真实接入口；RAG 已提供本地真实检索和 Milvus 混合检索接入口；Agent 主流程已用真实 LangGraph `StateGraph` 编排（`AGENT_ENGINE=langgraph`，缺失时回退顺序执行）。多模态模型、LangGraph checkpoint 持久化、RBAC 权限体系和外部数据库部署仍属于后续开发。住宿规则以**一晚**为例，真实审核还需要入住晚数、例外审批和制度生效日期。

## 演示与发布

演示步骤见 `docs/DEMO_GUIDE.md`。合并到 `main` 前的检查清单见 `docs/RELEASE_CHECKLIST.md`。

## 演示材料

演示与说明文档见 `docs/DEMO_GUIDE.md`。
