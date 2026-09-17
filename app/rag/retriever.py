from app.models.audit import PolicyEvidence
from .indexer import load_policy


def retrieve_policy(city: str | None) -> list[PolicyEvidence]:
    """Rule-backed baseline. Each limit is explicitly cited by chunk ID."""
    if not city:
        return []
    city = city.removesuffix("市")
    matches = []
    for item in load_policy():
        if city in item.content:
            matches.append(item.model_copy(update={"score": 1.0}))
        elif "其他城市" in item.content and city not in {"北京", "上海", "广州", "深圳"}:
            matches.append(item.model_copy(update={"score": 0.8}))
    return matches[:2]
