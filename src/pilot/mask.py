"""Masking, and where an unmasked copy is allowed to live.

The people a real run reads about did not ask to be researched. They published a
professional address so that someone with business would write to them, which is not the
same as agreeing to appear in a public demonstration next to a status that qualifies them.

So the rule is mechanical rather than a matter of care:

  * a real run writes its full output to a directory outside this repository. Nothing about a
    real person, and no address, is ever committed here or published on the page.
  * the controlled cases that this repository does commit are fictional. Their domains are
    under `.test`, a top level domain reserved by RFC 2606 that resolves for nobody, so a
    reader can verify at a glance that no mailbox of anyone's exists behind them.
  * masking happens where the shareable output is produced, not in a display component, so a
    file cannot be shared before a template has had a chance to hide anything.

`assert_no_real_address` is run by the test suite over every committed file, so the rule is
enforced by continuous integration rather than by remembering it.
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Domains an address may carry inside this repository. Everything else is a leak.
#   .test / .invalid / .example : reserved by RFC 2606, they resolve for nobody
#   example.com / .org / .net   : reserved by RFC 2606 for documentation
#   theaipipe.com and the git noreply addresses: ours
ALLOWED_SUFFIXES = (
    ".test", ".invalid", ".example", "example.com", "example.org", "example.net",
    "@theaipipe.com", "@users.noreply.github.com", "noreply@anthropic.com",
)


def mask_email(addr: str) -> str:
    """b***@domain.tld. The domain stays, because the domain is what a reader reasons about."""
    if not addr or "@" not in addr:
        return addr or ""
    local, _, dom = addr.partition("@")
    return f"{local[0]}***@{dom}" if local else f"***@{dom}"


def mask_text(text: str) -> str:
    """Mask every address in a blob, including the ones inside a quoted citation."""
    return EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text or "")


def real_addresses(text: str) -> list[str]:
    """Every address in a blob that is not masked and not on a reserved domain."""
    out = []
    for m in EMAIL_RE.finditer(text or ""):
        a = m.group(0)
        if "***" in a:
            continue
        low = a.lower()
        if any(low.endswith(s) for s in ALLOWED_SUFFIXES):
            continue
        out.append(a)
    return out


def assert_no_real_address(text: str, where: str = "") -> None:
    leaks = real_addresses(text)
    if leaks:
        raise AssertionError(f"an address that is not on a reserved domain appears in {where}: "
                             f"{[mask_email(a) for a in leaks[:3]]}")
