"""Locates the golden files the operator console records; they live in the console repository, not in this one."""

import os
from pathlib import Path

import pytest

GOLDEN_DIR = Path("backend/src/tests/fixtures")
GOLDEN_ENV_PREFIX = "AGENTEK_CONSOLE_GOLDEN_"


def find_golden(name: str) -> Path | None:
    """The console's fixture `name`; None when this directory is not inside a console checkout.

    AGENTEK_CONSOLE_GOLDEN_<NAME> (file name without extension, upper case) points at a copy elsewhere.
    """
    configured = os.environ.get(f"{GOLDEN_ENV_PREFIX}{Path(name).stem.upper()}")
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / GOLDEN_DIR / name
        if candidate.is_file():
            return candidate
    return None


def golden_file(name: str) -> Path:
    found = find_golden(name)
    if found is None:
        pytest.skip(
            f"console golden file {name} not found; run from a console checkout"
        )
    return found
