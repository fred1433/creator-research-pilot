# Control report

Commit `uncommitted`, written 2026-09-17 13:31 UTC by
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
| Observed | contact `h***@bramblewoodbakes.test`, row `verified`, in the sheet: true, draft: produced |

## 2. Injected case, through the same path

A contact proposed from outside, citing a real page and a sentence that is not on it.
Deliberately injected; the rejection is executed by the workflow.

| | |
|---|---|
| Test | `tests/test_controls.py::test_injected_finding_never_reaches_the_sheet_or_a_draft` |
| Input | the same file, the Fernhollow Forge case, one proposed contact whose quote is absent from the page it cites |
| Expected | rejected with `rejected: quote not found on the page`, no contact on the row, nothing in the sheet, no draft |
| Observed | verdicts ['rejected: quote not found on the page'], contact `(none)`, row `not found`, in the sheet: false, draft: blocked: there is no contact to write to |

## 3. Causal mutation

The same input, the same path, with `verify.verify_finding` neutralised and nothing else
changed. What fails is the assertion about the workflow's output, not an exit code.

| | |
|---|---|
| Test | `tests/test_controls.py::test_causal_mutation_without_the_guard_the_invented_address_is_published` |
| Change | one function returns `accepted` unconditionally |
| Expected | the assertion of control 2 becomes false |
| Observed | contact `p***@fernhollowforge.test`, row `verified`, in the sheet: true, draft: produced |

The guard is therefore the cause of the outcome in control 2, and not a step that happens to
sit next to it.

## Suite

```
23 passed in 0.13s
```
