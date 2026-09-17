"""Writes the control report: what was run, on which commit, expected and observed.

A claim that a check "is exercised" is worth nothing without the identifiers that let
somebody re-run it. This script produces those: the commit, the exact command, the test
identifier, what was expected and what was observed, taken from an actual execution of the
production path rather than transcribed by hand.

    python scripts/control_report.py           writes docs/control-report.md
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pilot import run as R                                   # noqa: E402
from pilot import verify                                     # noqa: E402
from pilot.drafts import render as render_draft              # noqa: E402
from pilot.emailcheck import ControlledChecker               # noqa: E402
from pilot.fetcher import ReplayFetcher                      # noqa: E402
from pilot.model import DEC_APPROVED                         # noqa: E402
from pilot.sheet import to_string                            # noqa: E402
from pilot.store import Store                                # noqa: E402

FIX = ROOT / "fixtures"
KEY = "controlled-key-not-a-secret"
INJECTED = "press@fernhollowforge.test"
POSITIVE = "hello@bramblewoodbakes.test"


def fresh():
    fetcher = ReplayFetcher(str(FIX / "sources"))
    checker = ControlledChecker(json.loads((FIX / "mailbox_responses.json").read_text()))
    return fetcher, checker, Store(":memory:")


def case(handle: str) -> dict:
    cases = json.loads((FIX / "controlled_cases.json").read_text())
    return next(c for c in cases if handle in c["url"])


def observe(handle: str, neutralise: bool = False) -> dict:
    fetcher, checker, store = fresh()
    original = verify.verify_finding
    if neutralise:
        verify.verify_finding = lambda finding, f: {"verdict": "accepted", "ok": True,
                                                    "content_sha256_now": ""}
    try:
        rec = R.process(case(handle), fetcher, checker, KEY)
    finally:
        verify.verify_finding = original
    store.upsert_channel(rec)
    payload = json.loads(store.get_channel(rec.channel_key)["payload"])
    draft = render_draft(payload, {"decision": DEC_APPROVED})
    return {
        "contact": rec.contact_value or "(none)",
        "summary": rec.summary(),
        "in_sheet": ("p***@fernhollowforge.test" in to_string(R.build_rows(store))
                     if handle == "fernhollowforge"
                     else "bramblewoodbakes.test" in to_string(R.build_rows(store))),
        "draft": "produced" if draft["ok"] else draft["reason"],
        "verdicts": [r.get("verdict") for r in rec.refused if r.get("finding")],
    }


def main() -> int:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or "uncommitted"
    pytest_run = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT,
                                capture_output=True, text=True)
    positive = observe("bramblewoodbakes")
    injected = observe("fernhollowforge")
    mutated = observe("fernhollowforge", neutralise=True)

    out = f"""# Control report

Commit `{commit}`, written {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by
`python scripts/control_report.py`. Every line below comes from executing the production
path, not from a transcription.

Re-run everything: `python -m pytest tests -q` and `python -m pilot.run replay --input
fixtures/controlled_cases.json --out data`.

## 1. Positive control

A contact proposed from outside the workflow, correctly sourced, must be accepted. A guard
that refuses everything would pass a suite that only tests refusals.

| | |
|---|---|
| Test | `tests/test_controls.py::test_positive_control_a_correctly_sourced_proposal_is_accepted` |
| Input | `fixtures/controlled_cases.json`, the Bramblewood Bakes case, one proposed contact |
| Expected | the proposal is accepted, the row reads `verified`, the sheet carries the contact, an approved row produces a draft |
| Observed | contact `{positive['contact'][0]}***@{positive['contact'].split('@')[-1]}`, row `{positive['summary']}`, in the sheet: {str(positive['in_sheet']).lower()}, draft: {positive['draft']} |

## 2. Injected case, through the same path

A contact proposed from outside, citing a real page and a sentence that is not on it.
Deliberately injected; the rejection is executed by the workflow.

| | |
|---|---|
| Test | `tests/test_controls.py::test_injected_finding_never_reaches_the_sheet_or_a_draft` |
| Input | the same file, the Fernhollow Forge case, one proposed contact whose quote is absent from the page it cites |
| Expected | rejected with `{verify.REJECTED_QUOTE_ABSENT}`, no contact on the row, nothing in the sheet, no draft |
| Observed | verdicts {injected['verdicts']}, contact `{injected['contact']}`, row `{injected['summary']}`, in the sheet: {str(injected['in_sheet']).lower()}, draft: {injected['draft']} |

## 3. Causal mutation

The same input, the same path, with `verify.verify_finding` neutralised and nothing else
changed. What fails is the assertion about the workflow's output, not an exit code.

| | |
|---|---|
| Test | `tests/test_controls.py::test_causal_mutation_without_the_guard_the_invented_address_is_published` |
| Change | one function returns `accepted` unconditionally |
| Expected | the assertion of control 2 becomes false |
| Observed | contact `{mutated['contact'][0]}***@{mutated['contact'].split('@')[-1]}`, row `{mutated['summary']}`, in the sheet: {str(mutated['in_sheet']).lower()}, draft: {mutated['draft']} |

The guard is therefore the cause of the outcome in control 2, and not a step that happens to
sit next to it.

## Suite

```
{pytest_run.stdout.strip().splitlines()[-1] if pytest_run.stdout.strip() else 'no output'}
```
"""
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "control-report.md").write_text(out, encoding="utf-8")
    print(out)
    return pytest_run.returncode


if __name__ == "__main__":
    sys.exit(main())
