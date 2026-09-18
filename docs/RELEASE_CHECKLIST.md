# 稳定版发布检查清单

合并 `develop` 到 `main` 前，必须完成以下检查。

## 1. 分支状态

```powershell
git switch develop
git pull --ff-only origin develop
git status --short --branch
```

要求：

- 当前分支为 `develop`
- 工作区干净
- 本地 `develop` 与 `origin/develop` 一致

## 2. 自动化检查

```powershell
.\.venv\Scripts\python.exe -m py_compile streamlit_app.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m app.evaluation.runner --format markdown
.\.venv\Scripts\python.exe -m app.services.model_smoke
```

当前预期：

- 单元测试全部通过；
- Milvus/bge-m3 集成测试在未配置环境时可跳过；
- 评测基线 5/5 通过；
- `model_smoke` 在 mock 模式下输出 `SKIPPED`。

## 3. 手动演示检查

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

在页面中逐个选择 5 个样例：

- [ ] 正常报销：三类材料齐全
- [ ] 住宿超标 + 主体不一致
- [ ] 缺少付款凭证
- [ ] 主体不一致但金额正常
- [ ] 发票日期超出行程

每个样例至少检查：

- [ ] 审核结论符合预期
- [ ] 风险卡片展示正确
- [ ] 关键字段展示原文证据
- [ ] 检查项表格可读
- [ ] 制度依据可解释
- [ ] Agent 执行轨迹可展开
- [ ] JSON 报告可下载

## 4. 合并到 main

```powershell
git switch main
git pull --ff-only origin main
git merge --no-ff develop -m "release: stable demo version"
git push origin main
```

如 `main` 与 `develop` 差异较大，合并前再次确认不要丢失 `develop` 上的新功能。

## 5. 发布后确认

```powershell
git log --oneline --decorate -5
git status --short --branch
```

要求：

- `main` 和 `origin/main` 指向稳定演示版；
- 工作区干净；
- README、DEMO_GUIDE、RELEASE_CHECKLIST 均存在。
