import json
from pathlib import Path
from app.models.audit import PolicyEvidence


POLICY_FILE = Path(__file__).resolve().parents[2] / "data" / "policies" / "travel_policy.json"


def load_policy() -> list[PolicyEvidence]:
    return [PolicyEvidence(**row, score=0.0) for row in json.loads(POLICY_FILE.read_text(encoding="utf-8"))]
