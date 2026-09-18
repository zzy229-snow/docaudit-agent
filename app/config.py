"""Environment loading helpers.

The project keeps real credentials out of git. Developers can copy
``.env.example`` to ``.env`` locally, and runtime entry points call
``load_environment`` before reading environment variables.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_environment(dotenv_path: str | Path | None = None) -> bool:
    path = Path(dotenv_path) if dotenv_path is not None else repo_root() / ".env"
    if not path.exists():
        return False
    return load_dotenv(dotenv_path=path, override=False)
