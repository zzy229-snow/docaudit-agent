"""必备材料检查(PRD §5.1 FR-101 / §8.3 ``required_documents``)。

材料类型**按票面内容判定优先、文件名兜底**(见 ``app.services.material_type``):
- ``material_types``:系统按内容判出的材料类型(每份"判得出类型"的材料一项);
- ``document_names``:仅那些"内容判不出类型"的材料文件名,用于关键词兜底。

两者取并集,所以「微信图片_2026.png」里识别出发票号码也算有发票,而不会因为文件名
不带 ``invoice`` 就被算成缺少发票。
"""
from app.models.audit import CheckResult
from app.services.material_type import FILENAME_HINTS, MATERIAL_LABELS, MATERIAL_TYPES


def check_required_documents(names: list[str], material_types: list[str] | None = None) -> CheckResult:
    present = {str(item).strip().lower() for item in (material_types or []) if str(item).strip()}
    lowered_names = [str(name).lower() for name in (names or [])]
    for material, keywords in FILENAME_HINTS.items():
        if any(keyword.lower() in name for name in lowered_names for keyword in keywords):
            present.add(material)
    missing = [MATERIAL_LABELS[material] for material in MATERIAL_TYPES if material not in present]
    return CheckResult(name="required_documents", passed=not missing,
                       detail="材料齐全" if not missing else "缺少：" + "、".join(missing))
