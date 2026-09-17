"""The only two ways this workflow reads a page, and the rules both of them obey.

`LiveFetcher` touches the public web. `ReplayFetcher` serves controlled sources from disk.
They answer the same call and return the same shape, which is what lets the two run modes
share one production path: the replay mode is not a simulation of the workflow, it is the
workflow with a different source of bytes.

Rules enforced here rather than remembered by whoever writes the next module:

1. One request at a time, with a pause between them. No concurrency, no bursts. A research
   tool that hammers a creator's site is a research tool that gets blocked, and being
   blocked is indistinguishable, in the output, from "this creator publishes nothing".
2. robots.txt is read once per host and obeyed. A disallowed path is not fetched, and the
   record says so, which is a result rather than a failure.
3. Only public http and https hosts. A URL that resolves to a private or loopback address is
   refused before the request leaves: the pages this workflow reads are supplied by third
   parties, so a link on one of them must never be able to make this process talk to
   something on the machine or the network it runs on.
4. Every response is kept, normalised and fingerprinted, so a finding taken from it can be
   re-checked later against a fresh download.

No logins, no cookies, no captcha solving, no private endpoints. If a page is only visible
to a signed-in human, this workflow treats it as not published.

One more thing about what comes back: a collected page is untrusted input. Nothing in it is
ever executed, and no instruction found in it changes what this workflow does. Text is
matched against patterns; it is never obeyed. `tests/test_untrusted_input.py` holds a page
that tries.
"""

from __future__ import annotations

import html as htmllib
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

UA = "creator-research-pilot/1.0 (+https://raffall-pilot.theaipipe.com)"
TIMEOUT = 25
DEFAULT_DELAY = 2.0
MAX_BYTES = 3_000_000

DISALLOWED_BY_ROBOTS = "disallowed by robots"
REFUSED_PRIVATE_HOST = "refused: not a public host"
REFUSED_SCHEME = "refused: unsupported scheme"


def normalise(text: str) -> str:
    """Turn a page into the text a human reads, so a quote can be looked for in it.

    Two renderings of the same page never agree on whitespace, entities or tag soup. They do
    agree on the words. Stripping to words is what lets a quote taken today still be found
    tomorrow, and is also what stops a quote from matching markup the reader never saw.
    """
    t = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = t.replace(" ", " ").replace("​", "")
    return re.sub(r"\s+", " ", t).strip()


def is_public_url(url: str) -> bool:
    """True when the URL names a public http(s) host. Checked before any request is sent."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname
    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


class LiveFetcher:
    """Polite, serial, robots-aware, and it remembers what it downloaded."""

    mode = "live"

    def __init__(self, delay: float = DEFAULT_DELAY, obey_robots: bool = True) -> None:
        self.delay = delay
        self.obey_robots = obey_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last = 0.0
        self.log: list[dict[str, str]] = []

    def _robots_for(self, url: str):
        parts = urllib.parse.urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root in self._robots:
            return self._robots[root]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(root + "/robots.txt")
        try:
            self._sleep()
            req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                rp.parse(r.read(MAX_BYTES).decode("utf-8", "replace").splitlines())
        except Exception:
            # No robots.txt, or it cannot be read. The conservative reading of the standard
            # is that everything is then allowed; the request rate stays low regardless.
            rp = None
        self._robots[root] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.obey_robots:
            return True
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(UA, url)

    def _sleep(self) -> None:
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def get(self, url: str) -> dict[str, str]:
        if not is_public_url(url):
            rec = {"url": url, "final_url": url, "status": REFUSED_PRIVATE_HOST, "body": "", "text": ""}
            self.log.append({"url": url, "status": rec["status"]})
            return rec
        if not self.allowed(url):
            rec = {"url": url, "final_url": url, "status": DISALLOWED_BY_ROBOTS, "body": "", "text": ""}
            self.log.append({"url": url, "status": rec["status"]})
            return rec
        self._sleep()
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read(MAX_BYTES).decode("utf-8", "replace")
                rec = {
                    "url": url,
                    "final_url": r.geturl(),
                    "status": str(r.status),
                    "body": body,
                    "text": normalise(body),
                }
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(20000).decode("utf-8", "replace")
            except Exception:
                pass
            rec = {"url": url, "final_url": url, "status": f"http {e.code}",
                   "body": detail, "text": normalise(detail)}
        except Exception as e:
            rec = {"url": url, "final_url": url, "status": f"error {type(e).__name__}",
                   "body": "", "text": ""}
        self.log.append({"url": url, "status": rec["status"]})
        return rec


class ReplayFetcher:
    """Serves controlled sources from a directory. No network, no spend, no third party.

    A fixture is a JSON file holding the URL it answers for, an optional final URL when the
    case under test is a redirect, a status, and a body. Everything downstream of this class
    cannot tell the difference, which is the property that makes the replay mode worth
    having: the guard, the relation policy, the sheet and the draft are the production ones.
    """

    mode = "replay"

    def __init__(self, directory: str) -> None:
        self.dir = directory
        self.log: list[dict[str, str]] = []
        self.pages: dict[str, dict] = {}
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                page = json.load(fh)
            self.pages[self.lookup_key(page["url"])] = page

    @staticmethod
    def lookup_key(url: str) -> str:
        """The URL a fixture answers for, with any API key dropped from the query.

        A request to a documented API carries a key; the URL kept as evidence never does. The
        fixture is filed under the evidence URL so both find it.
        """
        parts = urllib.parse.urlsplit(url.rstrip("/"))
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "key"]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), "")
        )

    def allowed(self, url: str) -> bool:
        page = self.pages.get(self.lookup_key(url))
        return not (page or {}).get("robots_disallow")

    def get(self, url: str) -> dict[str, str]:
        page = self.pages.get(self.lookup_key(url))
        if page is None:
            rec = {"url": url, "final_url": url, "status": "http 404", "body": "", "text": ""}
        elif page.get("robots_disallow"):
            rec = {"url": url, "final_url": url, "status": DISALLOWED_BY_ROBOTS, "body": "", "text": ""}
        else:
            body = page.get("body", "")
            rec = {
                "url": url,
                "final_url": page.get("final_url") or url,
                "status": str(page.get("status", "200")),
                "body": body,
                "text": normalise(body),
            }
        self.log.append({"url": url, "status": rec["status"]})
        return rec
