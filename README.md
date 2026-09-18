# DocAudit Agent

企业报销材料审核项目的稳定演示版。上传电子PDF、TXT、DOCX、XLSX、图片材料，系统会解析文本或走 Mock OCR 路由，提取明确标注的字段，检索演示制度并运行五项确定性检查。默认不需要GPU或模型API。

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

Streamlit 侧边栏也内置了 5 组虚构演示样例，可直接选择并点击“开始审核”，用于展示风险卡片、字段证据、制度依据、执行轨迹和 JSON 报告下载。

## FastAPI接口

```bash
python -m uvicorn app.api.main:app --reload --port 8102
```

接口文档地址：`http://127.0.0.1:8102/docs`。当前API使用内存任务存储，支持创建任务、上传材料、运行审核，以及查询字段、风险、执行轨迹和可解释摘要。详细说明见 `docs/API.md`。

## 已完成能力

- 五项确定性审核：必备材料、金额一致性、日期范围、住宿标准、主体一致性。
- 可解释报告：风险原因、字段证据、制度依据、检查项状态、Agent trace。
- Streamlit 演示页：内置 5 组虚构样例，支持 JSON 报告下载。
- FastAPI 任务接口：创建任务、上传材料、运行审核、字段修正、人工复核决策。
- LLM 接入护栏：JSON Schema 校验、原文证据校验、格式标准化、低置信拒收、重试和规则兜底。
- 评测基线：5 条端到端样例，可输出 JSON/Markdown 报告。

## 模型API

默认使用 `MODEL_PROVIDER=mock`，不需要API Key。真实模型接入通过 `app/services/model_gateway.py` 统一管理，字段抽取Prompt位于 `app/prompts/field_extraction_v1.txt`。LLM 字段抽取已加入 JSON Schema 校验、原文证据校验、格式标准化、低置信拒收、重试和规则兜底。配置说明见 `docs/MODEL_GATEWAY.md`，稳定性护栏说明见 `docs/LLM_ROBUSTNESS.md`。

购买真实API前，先复制 `.env.example` 为 `.env`。项目会自动读取仓库根目录下的 `.env`，但 `.env` 已被 `.gitignore` 排除，不要提交密钥。然后运行连通性检查：

```bash
python -m app.services.model_smoke
```

真实模型 API 购买和接入步骤见 `docs/MODEL_API_READINESS.md`。

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

此版是可解释的演示基线。默认 OCR、RAG、LLM 均可在 mock/offline 模式下运行；真实 OCR、多模态模型、生产级向量检索、LangGraph 持久化运行时、权限体系和数据库持久化仍属于后续开发。住宿规则以**一晚**为例，真实审核还需要入住晚数、例外审批和制度生效日期。

## 演示与发布

演示步骤见 `docs/DEMO_GUIDE.md`。合并到 `main` 前的检查清单见 `docs/RELEASE_CHECKLIST.md`。

## 演示材料

演示与说明文档见 `docs/DEMO_GUIDE.md`。
