from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pilot.emailcheck import ControlledChecker          # noqa: E402
from pilot.fetcher import ReplayFetcher                  # noqa: E402
from pilot.store import Store                            # noqa: E402

FIXTURES = ROOT / "fixtures"
REPLAY_KEY = "controlled-key-not-a-secret"


@pytest.fixture
def cases() -> list[dict]:
    return json.loads((FIXTURES / "controlled_cases.json").read_text(encoding="utf-8"))


@pytest.fixture
def fetcher() -> ReplayFetcher:
    return ReplayFetcher(str(FIXTURES / "sources"))


@pytest.fixture
def checker() -> ControlledChecker:
    return ControlledChecker(json.loads(
        (FIXTURES / "mailbox_responses.json").read_text(encoding="utf-8")))


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(str(tmp_path / "pilot.sqlite3"))


def case(cases: list[dict], handle: str) -> dict:
    return next(c for c in cases if handle in c["url"])
