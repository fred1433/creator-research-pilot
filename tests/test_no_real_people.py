"""Nothing about a real person is allowed into this repository, and the test says so.

A real run's full output goes to a directory outside the repository. What is committed here
is fictional, on domains reserved by RFC 2606 that resolve for nobody. This test walks every
tracked file, the git history included, and fails on any address that is neither masked nor
on a reserved domain.

It is here because masking is not the kind of rule that survives on attention. A demonstration
that publishes a real person's address, a real person's name next to a status qualifying them,
or either of those in a commit that a later commit cannot remove, has done something to
somebody who never agreed to it.
"""

from __future__ import annotations

import pathlib
import subprocess

from pilot.mask import assert_no_real_address, mask_email, real_addresses

ROOT = pathlib.Path(__file__).resolve().parent.parent
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".sqlite3", ".woff", ".woff2"}


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


def test_no_committed_file_carries_an_address_outside_a_reserved_domain():
    offenders = []
    for path in tracked_files():
        if path.suffix.lower() in BINARY or not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        leaks = real_addresses(text)
        if leaks:
            offenders.append((str(path.relative_to(ROOT)), [mask_email(a) for a in leaks[:3]]))
    assert not offenders, f"addresses outside reserved domains in tracked files: {offenders}"


def test_no_commit_in_the_history_carries_one_either():
    """An address removed by a later commit is still published by a public repository."""
    diff = subprocess.run(["git", "log", "-p", "--all", "--no-color"], cwd=ROOT,
                          capture_output=True, text=True).stdout
    leaks = real_addresses(diff)
    assert not leaks, f"addresses outside reserved domains in git history: " \
                      f"{[mask_email(a) for a in leaks[:3]]}"


def test_masking_keeps_the_domain_and_hides_the_mailbox():
    assert mask_email("someone@brand.test") == "s***@brand.test"
    assert_no_real_address("write to s***@brand.test", "a masked blob")


def test_the_shareable_output_is_masked_as_it_is_written(tmp_path):
    from pilot import run as R
    payload = {"rows": [], "channels": [{"contact_value": "someone@brand.example"}]}
    R.write_outputs(payload, str(tmp_path))
    written = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert "someone@brand.example" not in written
    assert "s***@brand.example" in written
