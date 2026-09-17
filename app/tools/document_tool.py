from app.models.audit import CheckResult


def check_required_documents(names: list[str]) -> CheckResult:
    names = [n.lower() for n in names]
    required = {"invoice": "发票", "payment": "付款凭证", "approval": "审批单"}
    missing = [label for keyword, label in required.items() if not any(keyword in n for n in names)]
    return CheckResult(name="required_documents", passed=not missing,
                       detail="材料齐全" if not missing else "缺少：" + "、".join(missing))
