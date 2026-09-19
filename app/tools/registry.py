from app.models.audit import PolicyEvidence
from app.tools.amount_tool import compare_amounts, compare_hotel_limit
from app.tools.date_tool import compare_date_range
from app.tools.document_tool import check_required_documents
from app.tools.duplicate_tool import check_duplicate_invoice
from app.tools.schema import RegisteredTool, ToolInputSchema, ToolSpec
from app.tools.subject_tool import compare_subject


def _required_documents(document_names: list[str]):
    return check_required_documents(document_names)


def _amount_match(invoice_amount: str | None, payment_amount: str | None):
    return compare_amounts(invoice_amount, payment_amount)


def _date_range(invoice_date: str | None, travel_start_date: str | None, travel_end_date: str | None):
    return compare_date_range(invoice_date, travel_start_date, travel_end_date)


def _hotel_limit(invoice_amount: str | None, policy_evidence: list[PolicyEvidence]):
    return compare_hotel_limit(invoice_amount, policy_evidence)


def _applicant_match(
    applicant_name: str | None,
    invoice_buyer: str | None = None,
    payment_party: str | None = None,
):
    return compare_subject(applicant_name, invoice_buyer, payment_party)


def _duplicate_invoice(
    invoice_number: str | None,
    invoice_date: str | None = None,
    invoice_amount: str | None = None,
    task_id: str | None = None,
):
    return check_duplicate_invoice(invoice_number, invoice_date, invoice_amount, task_id)


TOOL_REGISTRY: dict[str, RegisteredTool] = {
    "required_documents": RegisteredTool(
        spec=ToolSpec(
            name="required_documents",
            description="检查差旅报销是否包含发票、付款凭证和审批单",
            category="document",
            inputs=[ToolInputSchema(name="document_names", description="上传材料文件名列表")],
        ),
        handler=_required_documents,
    ),
    "amount_match": RegisteredTool(
        spec=ToolSpec(
            name="amount_match",
            description="比较发票金额与付款金额是否一致",
            category="amount",
            inputs=[
                ToolInputSchema(name="invoice_amount", description="发票金额"),
                ToolInputSchema(name="payment_amount", description="付款金额"),
            ],
        ),
        handler=_amount_match,
    ),
    "date_range": RegisteredTool(
        spec=ToolSpec(
            name="date_range",
            description="检查发票日期是否落在出差开始和结束日期之间",
            category="date",
            inputs=[
                ToolInputSchema(name="invoice_date", description="发票日期"),
                ToolInputSchema(name="travel_start_date", description="出差开始日期"),
                ToolInputSchema(name="travel_end_date", description="出差结束日期"),
            ],
        ),
        handler=_date_range,
    ),
    "hotel_limit": RegisteredTool(
        spec=ToolSpec(
            name="hotel_limit",
            description="根据制度证据检查住宿金额是否超过标准",
            category="policy",
            inputs=[
                ToolInputSchema(name="invoice_amount", description="发票金额"),
                ToolInputSchema(name="policy_evidence", description="制度检索证据"),
            ],
        ),
        handler=_hotel_limit,
    ),
    "applicant_match": RegisteredTool(
        spec=ToolSpec(
            name="applicant_match",
            description="检查申请人、发票购买方和付款收款方是否一致",
            category="document",
            inputs=[
                ToolInputSchema(name="applicant_name", description="申请人/报销人姓名"),
                ToolInputSchema(name="invoice_buyer", description="发票购买方/抬头", required=False),
                ToolInputSchema(name="payment_party", description="付款凭证收款方/收款人", required=False),
            ],
        ),
        handler=_applicant_match,
    ),
    "check_duplicate_invoice": RegisteredTool(
        spec=ToolSpec(
            name="check_duplicate_invoice",
            description="按发票号码+开票日期+金额查询历史记录,命中时返回关联任务编号",
            category="database",
            inputs=[
                ToolInputSchema(name="invoice_number", description="发票号码"),
                ToolInputSchema(name="invoice_date", description="开票日期", required=False),
                ToolInputSchema(name="invoice_amount", description="发票金额", required=False),
                ToolInputSchema(name="task_id", description="当前任务ID,用于排除自身记录", required=False),
            ],
        ),
        handler=_duplicate_invoice,
    ),
}


def get_tool(name: str) -> RegisteredTool:
    try:
        return TOOL_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown tool: {name}") from exc


def list_tool_specs() -> list[ToolSpec]:
    return [tool.spec for tool in TOOL_REGISTRY.values()]
