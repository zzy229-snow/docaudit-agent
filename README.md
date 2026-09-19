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

打开页面后，分别上传 `data/demo/normal/` 下的三份TXT，可得到 `PASS`；上传 `data/demo/over_limit/` 下的三份TXT，可得到住宿超标80元、发票购买方与申请人不一致和制度引用。文件名应包含 `invoice`、`payment`、`approval`，作为当前材料类型识别依据。请勿将真实敏感材料上传到公开部署页面。

Streamlit 页面已升级为审核工作台形态：左侧可创建 5 组虚构样例任务，主界面包含任务中心、新建审核、审核详情、风险看板、字段证据、人工复核、事件时间线、HTML 正式报告和 JSON 审核包下载。

## FastAPI接口

```bash
python -m uvicorn app.api.main:app --reload --port 8102
```

接口文档地址：`http://127.0.0.1:8102/docs`。当前API默认使用本地 SQLite 任务存储，数据落在 `data/runtime/audit_tasks.sqlite3`，支持创建任务、上传材料、运行审核、任务列表、生命周期事件、字段修正、人工复核决策，以及查询字段、风险、执行轨迹和可解释摘要。详细说明见 `docs/API.md`。

## 已完成能力

- 五项确定性审核：必备材料、金额一致性、日期范围、住宿标准、主体一致性。
- 可解释报告：风险原因、字段证据、制度依据、检查项状态、Agent trace。
- Streamlit 演示页：内置 5 组虚构样例，支持 JSON 报告下载。
- FastAPI 任务接口：创建任务、上传材料、运行审核、字段修正、人工复核决策。
- 生产化任务基础：SQLite 持久化任务、材料、报告、人工修正、复核项和审计事件。
- 报告导出：支持通过 API 和 Streamlit 下载 HTML 正式审核报告。
- LLM 接入护栏：JSON Schema 校验、原文证据校验、格式标准化、低置信拒收、重试和规则兜底。
- 评测基线：5 条端到端样例，可输出 JSON/Markdown 报告。

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

此版是可解释的演示基线。默认 OCR、RAG、LLM 均可在 mock/offline 模式下运行；任务和审核结果已支持本地 SQLite 持久化。OCR 已提供 Tesseract、HTTP OCR、MinerU 三类真实接入口；RAG 已提供本地真实检索和 Milvus 混合检索接入口；多模态模型、LangGraph 持久化运行时、权限体系和外部数据库部署仍属于后续开发。住宿规则以**一晚**为例，真实审核还需要入住晚数、例外审批和制度生效日期。

## 演示与发布

演示步骤见 `docs/DEMO_GUIDE.md`。合并到 `main` 前的检查清单见 `docs/RELEASE_CHECKLIST.md`。

## 演示材料

演示与说明文档见 `docs/DEMO_GUIDE.md`。
