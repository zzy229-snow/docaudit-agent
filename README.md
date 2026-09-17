# DocAudit Agent

企业报销材料审核项目的首个可运行版本。上传电子PDF、TXT、DOCX、XLSX材料，提取明确标注的字段，检索演示制度并运行四项确定性检查。图片及扫描PDF尚未接入OCR，会明确报错；当前不需要GPU或模型API。

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

打开页面后，分别上传 `data/demo/normal/` 下的三份TXT，可得到 `PASS`；上传 `data/demo/over_limit/` 下的三份TXT，可得到住宿超标80元和制度引用。文件名应包含 `invoice`、`payment`、`approval`，作为当前材料类型识别依据。请勿将真实敏感材料上传到公开部署页面。

## FastAPI接口

```bash
python -m uvicorn app.api.main:app --reload --port 8102
```

接口文档地址：`http://127.0.0.1:8102/docs`。当前API使用内存任务存储，支持创建任务、上传材料、运行审核，以及查询字段、风险和执行轨迹。详细说明见 `docs/API.md`。

## 测试

```bash
python -m unittest discover -s tests -v
```

## 目录

`app/models` 是统一数据结构；`app/parsers` 解析文件；`app/extraction` 抽字段；`app/rag` 读取和匹配演示制度；`app/tools` 精确校验；`app/agent` 串联审核流程；`app/services` 预留模型API接口；`data` 为虚构样例和预期结果。

## 当前限制

此版是可解释的规则基线。OCR、多模态模型、向量检索、LangGraph运行时和交互式人工修正属于后续开发，不能将当前版本描述为已实现这些能力。住宿规则以**一晚**为例，真实审核还需要入住晚数、例外审批和制度生效日期。

## 推送到你的Gitee仓库

下载压缩包、解压后，在 `docaudit-agent` 目录打开终端：

```bash
git init
git add .
git commit -m "feat: initial runnable audit baseline"
git branch -M main
git remote add origin https://gitee.com/zzy229-snow/docaudit_-agent.git
git push -u origin main
```

如果仓库创建时已初始化README，应先克隆仓库，再将解压的项目文件复制进去并提交，以免推送时遇到不同历史。密钥只保存在本地 `.env`，不要提交仓库。
