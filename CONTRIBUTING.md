# 贡献指南

欢迎提 Issue 与 Pull Request。这是一个**可解释审核**的演示项目，改动优先看两件事：结论是否仍然可解释、测试与评测基线是否仍然全绿。

## 环境准备

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

重型可选依赖（Milvus 向量检索）单独装：`pip install -r requirements-rag.txt`。

## 提交前必须做的检查

```bash
python -m unittest discover -s tests        # 必须全绿（当前 338 例，全离线）
python -m app.evaluation.runner --format markdown   # 评测基线不能被拉低
```

- **测试必须离线且确定性**：单测进程不读 `.env`（`app.config.load_environment` 会跳过），需要真实引擎/网络的用例必须显式 `skipUnless` 或用 `patch.dict` 指定。
- **测试不许碰生产数据**：任务库、发票查重登记表、已发布制度库在单测里一律指向临时目录（闸门在 `app/api/store.py` 的 `_default_db_path()`）。加新测试时不要绕过它。
- 新增/修改风险规则时，同步更新 `docs/` 与评测用例。

## 代码约定

- 注释与文档用中文，**解释"为什么"**而不是复述"做了什么"；关键取舍写在 docstring 里。
- 结论只由确定性工具产出，LLM 只负责"读材料、补字段"；不要引入由 LLM 直接决定的 `PASS` / `REVIEW_REQUIRED`。
- 字段抽取的优先级固定为：人工修正 > OCR 接口结构化字段 > LLM 抽取 > 版式正则。
- 解析类改动请补一条**真实版式**的回归测试（真实票据的排版经常和示例不一样）。

## 不要提交这些

- 任何真实数据：真实发票号/税号/公司名/人名，真实发票图片（仓库里的样例全部是虚构或脱敏的）。
- 任何密钥：`.env` 已被忽略，请只改 `.env.example` 里的占位符。
- 本机私有路径：默认值要么走环境变量，要么留空并给可操作的报错，不要写死别人的机器路径。
- 大体积第三方二进制（模型权重、语言包）：写进 README 说明获取方式即可。

## 分支与提交信息

- 主开发分支 `develop`，`main` 只放可发布状态；改 `main` 前先讨论。
- 提交信息用 `<type>(<scope>): <描述>`，type 取 `feat` / `fix` / `docs` / `chore` / `refactor` / `test`。
- 涉及对外契约的改动（`app/models/` 的数据结构、`run_audit` 签名、API 请求/响应）请在提交信息里单独写一段说明，便于评审。

## License

贡献的代码默认按仓库的 Apache-2.0 许可发布（见 `LICENSE`）。
