"""Mailbox verification, and the words this workflow refuses to say.

Two steps, in order, because the cheap one settles a whole class of cases:

1. MX records. A domain with no mail server receives nothing, including at the address
   printed on its own homepage. One DNS query, no credits.
2. A real check at the mail server, through the verification vendor. Its status is recorded
   verbatim, never re-labelled, so a reviewer who knows the vendor's vocabulary can read the
   column without learning ours.

Three refusals worth stating, because each one is a way a research tool lies:

  * `catch_all` is not "valid". A catch-all domain accepts every address at the protocol
    level and decides later, so the check concluded nothing about this mailbox. The sentence
    the reviewer is shown says exactly that. Calling it valid is how a workflow produces an
    address that bounces.
  * a suggested correction never replaces a published address. The vendor may answer that
    someone probably meant something else; the workflow keeps what the creator published and
    carries the suggestion beside it, for a human. Silently rewriting a published address is
    inventing one.
  * a demonstration key is not a check. `livemode` says whether a credit was actually spent
    against the real service; when it is false the result is `not_checked`, which is an
    absence of measurement and never a verdict. An exhausted quota gives `not_checked` too.

Rate discipline: one address at a time, and never the same address twice in quick
succession. A second probe of the same mailbox within a minute is answered by the tarpit
large mail hosts put in front of repeat callers, which turns a good answer into a useless
one. The cache below exists for that reason, not for speed.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

from .model import CHK_NOT_CHECKED

API = "https://api.millionverifier.com/api/v3/"
USER_AGENT = "creator-research-pilot/1.0"
TIMEOUT = 40


def mx_hosts(domain: str) -> list[str]:
    """MX lookup through the system resolver. No MX means no mail, full stop."""
    try:
        out = subprocess.run(
            ["dig", "+short", "MX", domain], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except Exception:
        return []
    hosts = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            hosts.append(parts[1].rstrip("."))
    return hosts


class MailboxChecker:
    """Wraps the verification vendor. The key is read from the environment, never printed.

    `live=False` is the default: it returns `not_checked` without spending anything, which is
    what the replay mode and any re-run of frozen results use. A credit is spent only on a
    first, real look at an address, in live mode, with a key present.
    """

    def __init__(self, api_key: str = "", live: bool = False, min_gap: float = 65.0,
                 resolver=mx_hosts) -> None:
        self.key = api_key or os.environ.get("MILLIONVERIFIER_API_KEY", "")
        self.live = bool(live and self.key)
        self.min_gap = min_gap
        self.resolver = resolver
        self.spent = 0
        self._seen: dict[str, dict] = {}
        self._last_probe: dict[str, float] = {}

    def check(self, address: str) -> dict:
        addr = (address or "").strip()
        if not addr or "@" not in addr:
            return {"address": addr, "result": CHK_NOT_CHECKED, "livemode": False,
                    "reason": "no address to check"}

        key = addr.lower()
        if key in self._seen:
            # Already answered in this run. Asking again would only measure our own cadence.
            return dict(self._seen[key], cached=True)

        domain = key.partition("@")[2]
        mx = self.resolver(domain)
        if not mx:
            out = {"address": addr, "result": "no_mx", "mx": "", "livemode": False,
                   "reason": "the domain has no mail server, so it receives nothing"}
            self._seen[key] = out
            return out

        if not self.live:
            out = {"address": addr, "result": CHK_NOT_CHECKED, "mx": mx[0], "livemode": False,
                   "reason": "no verification was performed on this run"}
            self._seen[key] = out
            return out

        last = self._last_probe.get(key)
        if last and time.time() - last < self.min_gap:
            time.sleep(self.min_gap - (time.time() - last))

        url = API + "?" + urllib.parse.urlencode({"api": self.key, "email": addr, "timeout": 20})
        # The vendor sits behind a filter that refuses a request with no user agent, which
        # arrives as a 403 and reads exactly like a bad key. Naming the caller costs nothing
        # and removes a whole class of wrong diagnosis.
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            out = {"address": addr, "result": CHK_NOT_CHECKED, "mx": mx[0], "livemode": False,
                   "reason": f"the check did not complete: {type(e).__name__}"}
            self._seen[key] = out
            return out

        self._last_probe[key] = time.time()
        self.spent += 1
        error = str(d.get("error") or "")
        result = d.get("result", "unknown")
        if error:
            result = CHK_NOT_CHECKED
        out = {
            "address": addr,
            "result": result,
            "subresult": d.get("subresult", ""),
            # Carried beside the published address, never in its place.
            "suggestion": d.get("didyoumean", ""),
            "mx": mx[0],
            "livemode": not error,
            "reason": error or reading(result),
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._seen[key] = out
        return out


def reading(result: str) -> str:
    """Plain English for a reviewer, without ever promoting a status to something it is not."""
    return {
        "ok": "the mail server accepts this mailbox",
        "catch_all": "the check cannot conclude on this mailbox: the domain accepts every address",
        "invalid": "the mail server rejects this mailbox",
        "disposable": "a throwaway mailbox provider",
        "unknown": "the check did not complete",
        "no_mx": "the domain has no mail server, so it receives nothing",
        CHK_NOT_CHECKED: "no verification was performed on this run",
    }.get(result, result)


class ControlledChecker:
    """The verification step, answered from a controlled file instead of the vendor.

    It exists so the replay mode can exercise the whole path, including what the workflow
    does with each possible answer, without spending a credit and without probing a mailbox
    that belongs to somebody. Every answer it returns carries `livemode: false`, so nothing
    downstream, and no reader of the sheet, can mistake it for a real check.
    """

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = {k.lower(): v for k, v in responses.items()}
        self.spent = 0
        self.live = False

    def check(self, address: str) -> dict:
        addr = (address or "").strip()
        if not addr or "@" not in addr:
            return {"address": addr, "result": CHK_NOT_CHECKED, "livemode": False,
                    "reason": "no address to check"}
        d = self.responses.get(addr.lower())
        if d is None:
            return {"address": addr, "result": CHK_NOT_CHECKED, "livemode": False,
                    "reason": "no controlled response for this address"}
        result = d.get("result", CHK_NOT_CHECKED)
        return {
            "address": addr,
            "result": result,
            "mx": d.get("mx", "mx.controlled.test"),
            "livemode": False,
            "suggestion": d.get("didyoumean", ""),
            "reason": "controlled vendor response, no credit spent: " + reading(result),
        }
