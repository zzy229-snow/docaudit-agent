"""生成 50 例评测数据(任务⑤)。

程序化生成 demo 材料目录 + cases.json 标注,保证每个案例真实可运行、断言精确。
用法: python scripts/generate_eval_cases.py

产出:
    data/eval_generated/<case_id>/   (每例 3 份 TXT 或 2 份)
    data/evaluation/cases_generated.json
"""
from __future__ import annotations

import json
from collections import Counter
from itertools import count
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "eval_generated"
OUT_CASES = REPO / "data" / "evaluation" / "cases_generated.json"
CASES_JSON = REPO / "data" / "evaluation" / "cases.json"

#: 每例使用唯一发票号码,避免与查重(FR-204)互相干扰
_INVOICE_SEQ = count(90001)
DUPLICATE_INVOICE_NUMBER = "TEST-2026-99001"


def next_invoice_number() -> str:
    return f"TEST-2026-{next(_INVOICE_SEQ)}"


FIRST_TIER_LIMIT = 600
OTHER_LIMIT = 450

# (城市, 分档上限, 对应制度条款)
CITIES = [
    ("北京", FIRST_TIER_LIMIT, "TRAVEL-V1-4.2-A"),
    ("上海", FIRST_TIER_LIMIT, "TRAVEL-V1-4.2-A"),
    ("深圳", FIRST_TIER_LIMIT, "TRAVEL-V1-4.2-A"),
    ("杭州", OTHER_LIMIT, "TRAVEL-V1-4.2-B"),
    ("成都", OTHER_LIMIT, "TRAVEL-V1-4.2-B"),
    ("武汉", OTHER_LIMIT, "TRAVEL-V1-4.2-B"),
]

APPLICANT = "张三"
OTHER_PERSON = "李四"


def _fmt(v) -> str:
    return f"{v:.2f}"


def build_case(case_id, name, city, invoice_amount, payment_amount, invoice_date,
               start, end, applicant, buyer, payee,
               expected_status, expected_risks, missing=None,
               invoice_number=None, policy_refs=None) -> dict:
    """构造一个案例:写入材料目录,返回 case 标注 dict。"""
    missing = missing or []
    invoice_number = invoice_number or next_invoice_number()
    files = {}
    files["invoice.txt"] = (
        f"发票号码：{invoice_number}\n"
        f"发票日期：{invoice_date}\n"
        f"发票金额：{_fmt(invoice_amount)}\n"
        f"购买方：{buyer}\n"
        "测试样例，非真实票据，不得用于报销。\n"
    )
    files["payment.txt"] = (
        f"付款金额：{_fmt(payment_amount)}\n"
        f"收款方：{payee}\n"
        "测试样例，非真实支付凭证。\n"
    )
    files["approval.txt"] = (
        f"申请人：{applicant}\n"
        f"出差城市：{city}\n"
        f"出差开始：{start}\n"
        f"出差结束：{end}\n"
        "测试样例，非真实审批单。\n"
    )
    for f in missing:
        files.pop(f, None)

    case_dir = OUT_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (case_dir / fname).write_text(content, encoding="utf-8")

    fields = {
        "invoice_number": invoice_number,
        "invoice_amount": _fmt(invoice_amount),
        "payment_amount": _fmt(payment_amount),
        "invoice_date": invoice_date,
        "travel_city": city,
        "travel_start_date": start,
        "travel_end_date": end,
        "applicant_name": applicant,
        "invoice_buyer": buyer,
        "payment_party": payee,
    }
    if "payment.txt" in missing:
        fields.pop("payment_amount", None)
        fields.pop("payment_party", None)
    if "invoice.txt" in missing:
        for k in list(fields):
            if k in ("invoice_amount", "invoice_date", "invoice_buyer", "invoice_number"):
                fields.pop(k, None)
    if "approval.txt" in missing:
        for k in ("applicant_name", "travel_city", "travel_start_date", "travel_end_date"):
            fields.pop(k, None)

    return {
        "case_id": case_id,
        "name": name,
        "documents_dir": f"data/eval_generated/{case_id}",
        "expected_status": expected_status,
        "expected_risks": sorted(expected_risks),
        "expected_fields": fields,
        "expected_policy_refs": sorted(policy_refs or []),
    }


def gen_all() -> list[dict]:
    cases = []

    def add(obj):
        cases.append(obj)

    # ---- 正常 PASS(6 城市 x 2)----
    for city, limit, pref in CITIES:
        for idx in range(2):
            amt = 520 if idx == 0 else 480
            if limit == OTHER_LIMIT:
                amt = 380 if idx == 0 else 340
            tag = "T1" if pref.endswith("A") else "T2"
            add(build_case(
                f"CASE_{tag}_NORM_{idx+1:02d}_{city}", f"正常报销-{city}", city,
                amt, amt, "2026-08-01", "2026-08-01", "2026-08-02",
                APPLICANT, APPLICANT, APPLICANT, "PASS", [], policy_refs=[pref],
            ))

    # ---- 超标 HOTEL_LIMIT ----
    for city, limit, pref in CITIES[:3]:
        for idx, over in enumerate([0.01, 80, 300]):
            amt = limit + over
            add(build_case(
                f"CASE_T1_LIMIT_{idx+1:02d}_{city}", f"住宿超标-{city}+{over}", city,
                amt, amt, "2026-08-01", "2026-08-01", "2026-08-02",
                APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["HOTEL_LIMIT"],
                policy_refs=[pref],
            ))
    for city, limit, pref in CITIES[3:]:
        for idx, over in enumerate([0.01, 80]):
            amt = limit + over
            add(build_case(
                f"CASE_T2_LIMIT_{idx+1:02d}_{city}", f"住宿超标-{city}+{over}", city,
                amt, amt, "2026-08-01", "2026-08-01", "2026-08-02",
                APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["HOTEL_LIMIT"],
                policy_refs=[pref],
            ))

    # ---- 金额不符 ----
    for idx, (inv, pay) in enumerate([(580, 500), (450, 400), (350, 380)]):
        add(build_case(
            f"CASE_AMOUNT_{idx+1:02d}", f"金额不符-发票{inv}付款{pay}", "北京",
            inv, pay, "2026-08-01", "2026-08-01", "2026-08-02",
            APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["AMOUNT_MATCH"],
            policy_refs=["TRAVEL-V1-4.2-A"],
        ))

    # ---- 日期越界 ----
    for idx, (inv_date, start, end) in enumerate([
        ("2026-08-15", "2026-08-01", "2026-08-02"),
        ("2026-07-30", "2026-08-01", "2026-08-02"),
        ("2026-08-03", "2026-08-01", "2026-08-02"),
        ("2026-02-31", "2026-02-01", "2026-02-28"),
    ]):
        add(build_case(
            f"CASE_DATE_{idx+1:02d}", f"日期问题-{inv_date}", "北京",
            500, 500, inv_date, start, end,
            APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["DATE_RANGE"],
            policy_refs=["TRAVEL-V1-4.2-A"],
        ))

    # ---- 主体不符 ----
    for idx, (buyer, payee) in enumerate([(OTHER_PERSON, APPLICANT), (APPLICANT, OTHER_PERSON)]):
        add(build_case(
            f"CASE_SUBJECT_{idx+1:02d}", f"主体不符-购买方{buyer}收款方{payee}", "北京",
            500, 500, "2026-08-01", "2026-08-01", "2026-08-02",
            APPLICANT, buyer, payee, "REVIEW_REQUIRED", ["APPLICANT_MATCH"],
            policy_refs=["TRAVEL-V1-4.2-A"],
        ))

    # ---- 缺材料 + 级联风险 ----
    missing_cases = [
        (["payment.txt"], ["REQUIRED_DOCUMENTS", "AMOUNT_MATCH"]),
        (["approval.txt"], ["REQUIRED_DOCUMENTS", "APPLICANT_MATCH", "DATE_RANGE", "HOTEL_LIMIT"]),
        (["invoice.txt"], ["REQUIRED_DOCUMENTS", "AMOUNT_MATCH", "DATE_RANGE", "HOTEL_LIMIT"]),
    ]
    for idx, (miss, risks) in enumerate(missing_cases):
        add(build_case(
            f"CASE_MISSING_{idx+1:02d}", f"缺材料-{miss[0]}", "北京",
            500, 500, "2026-08-01", "2026-08-01", "2026-08-02",
            APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", risks, missing=miss,
        ))

    # ---- 组合 ----
    add(build_case(
        "CASE_COMBO_01", "超标且主体不符", "北京",
        FIRST_TIER_LIMIT + 80, FIRST_TIER_LIMIT + 80, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, OTHER_PERSON, OTHER_PERSON, "REVIEW_REQUIRED", ["HOTEL_LIMIT", "APPLICANT_MATCH"],
        policy_refs=["TRAVEL-V1-4.2-A"],
    ))
    add(build_case(
        "CASE_COMBO_02", "金额不符且日期越界", "北京",
        580, 460, "2026-08-10", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["AMOUNT_MATCH", "DATE_RANGE"],
        policy_refs=["TRAVEL-V1-4.2-A"],
    ))

    # ---- 边界 PASS(恰等于上限)----
    add(build_case(
        "CASE_BOUNDARY_01", "一线城市住宿恰等于600", "北京",
        FIRST_TIER_LIMIT, FIRST_TIER_LIMIT, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT, "PASS", [], policy_refs=["TRAVEL-V1-4.2-A"],
    ))
    add(build_case(
        "CASE_BOUNDARY_02", "其他城市住宿恰等于450", "成都",
        OTHER_LIMIT, OTHER_LIMIT, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT, "PASS", [], policy_refs=["TRAVEL-V1-4.2-B"],
    ))
    add(build_case(
        "CASE_BOUNDARY_03", "一线住宿恰等于600且多日", "广州",
        FIRST_TIER_LIMIT, FIRST_TIER_LIMIT, "2026-08-03", "2026-08-01", "2026-08-03",
        APPLICANT, APPLICANT, APPLICANT, "PASS", [], policy_refs=["TRAVEL-V1-4.2-A"],
    ))

    # ---- 大额超标(其他城市 +300)----
    for city, limit in [(c, l) for c, l, _ in CITIES[3:]]:
        amt = limit + 300
        add(build_case(
            f"CASE_T2_BIG_{city}", f"其他城市大幅超标-{city}", city,
            amt, amt, "2026-08-01", "2026-08-01", "2026-08-02",
            APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["HOTEL_LIMIT"],
            policy_refs=[next(p for c2, l2, p in CITIES if c2 == city)],
        ))

    # ---- 组合(缺材料 + 其他风险)----
    add(build_case(
        "CASE_COMBO_03", "缺审批且主体不符", "北京",
        520, 520, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, OTHER_PERSON, OTHER_PERSON,
        "REVIEW_REQUIRED", ["APPLICANT_MATCH", "REQUIRED_DOCUMENTS", "DATE_RANGE", "HOTEL_LIMIT"],
        missing=["approval.txt"], policy_refs=[],
    ))
    add(build_case(
        "CASE_COMBO_04", "缺发票且日期越界", "北京",
        600, 600, "2026-08-20", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT,
        "REVIEW_REQUIRED", ["AMOUNT_MATCH", "DATE_RANGE", "HOTEL_LIMIT", "REQUIRED_DOCUMENTS"],
        missing=["invoice.txt"], policy_refs=[],
    ))
    add(build_case(
        "CASE_COMBO_05", "付款方与申请人不符且住宿超其他城市", "成都",
        500, 500, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, OTHER_PERSON, "REVIEW_REQUIRED", ["APPLICANT_MATCH", "HOTEL_LIMIT"],
        policy_refs=["TRAVEL-V1-4.2-B"],
    ))

    # ---- 重复发票查重(FR-204):同一发票号码连续提交三例 ----
    add(build_case(
        "CASE_DUP_01", "首次提交发票(登记历史)", "北京",
        520, 520, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT, "PASS", [],
        invoice_number=DUPLICATE_INVOICE_NUMBER, policy_refs=["TRAVEL-V1-4.2-A"],
    ))
    add(build_case(
        "CASE_DUP_02", "重复提交同一发票", "北京",
        520, 520, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["DUPLICATE_INVOICE"],
        invoice_number=DUPLICATE_INVOICE_NUMBER, policy_refs=["TRAVEL-V1-4.2-A"],
    ))
    add(build_case(
        "CASE_DUP_03", "同一发票号码但金额不一致", "北京",
        480, 480, "2026-08-01", "2026-08-01", "2026-08-02",
        APPLICANT, APPLICANT, APPLICANT, "REVIEW_REQUIRED", ["INVOICE_NUMBER_CONFLICT"],
        invoice_number=DUPLICATE_INVOICE_NUMBER, policy_refs=["TRAVEL-V1-4.2-A"],
    ))

    return cases


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = gen_all()
    payload = json.dumps(cases, ensure_ascii=False, indent=2)
    # cases.json 是评测实际读取的文件,cases_generated.json 保留生成产物,两者保持一致
    OUT_CASES.write_text(payload, encoding="utf-8")
    CASES_JSON.write_text(payload, encoding="utf-8")
    ids = [c["case_id"] for c in cases]
    dup = {x for x in ids if ids.count(x) > 1}
    print(f"生成 {len(cases)} 例 -> {CASES_JSON}")
    print(f"唯一 case_id: {len(set(ids))}")
    if dup:
        print(f"!! 重复 case_id: {dup}")
    risk = Counter(frozenset(c["expected_risks"]) for c in cases)
    print("风险分布:", dict(risk))
    numbers = [c["expected_fields"].get("invoice_number") for c in cases]
    dup_numbers = {n for n in numbers if n and numbers.count(n) > 1}
    print("唯一发票号码:", len({n for n in numbers if n}), "重复号码(预期只含查重样例):", dup_numbers)
    missing_dirs = [c["case_id"] for c in cases if not (OUT_DIR / c["case_id"]).exists()]
    if missing_dirs:
        print("!! 缺失目录:", missing_dirs)


if __name__ == "__main__":
    main()
