"""The channel step, through the documented API and through nothing else.

`channels.list` costs one unit and answers with the channel id, the title, the description
and the custom handle. It can be asked by handle, by legacy user name or by id. What it does
not return is a business email field, and it does not return the collection of external
links a channel displays: an address or a site may appear inside the description, and that
is the whole of what the documented route offers on this subject.

There is no fallback. Reading YouTube's own pages with a script is outside what its
developer terms allow, so when the key is missing or the key has no access to this API the
workflow says so, in the words the API used, and the channel goes to review as
`access_blocked`. That is a limit, it is visible, and it is not worked around.

The addresses behind YouTube's own access mechanism are excluded from automated collection.
No captcha is bypassed. The limits encountered stay visible in the review.

When a site is known by other means, a human may supply it. The workflow then records it as
`manual_input` and counts it, so nobody can mistake a hand-fed URL for a collected one.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from .model import Finding

API = "https://www.googleapis.com/youtube/v3/channels"

NO_KEY = "no GOOGLE_API_KEY in the environment"
NO_ACCESS = "the key in the environment has no access to the YouTube Data API"


def handle_of(url_or_handle: str) -> str:
    """The handle a channel URL carries, or the string itself when it is already one."""
    s = (url_or_handle or "").strip()
    m = re.search(r"youtube\.com/@([A-Za-z0-9._\-]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"youtube\.com/(?:c|user)/([A-Za-z0-9._\-]+)", s)
    if m:
        return m.group(1)
    return s.lstrip("@") if not s.startswith("http") else ""


def channel_id_of(url: str) -> str:
    m = re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_\-]{20,})", url or "")
    return m.group(1) if m else ""


def channels_list(channel_url: str, api_key: str, fetcher) -> dict:
    """One documented call. Returns what it answered, whether or not that was data.

    The failure case carries the API's own message, because "we could not read this channel"
    is worth nothing to a reviewer and "this key is blocked for this method" is worth a
    decision.
    """
    if not api_key:
        return {"ok": False, "reason": NO_KEY, "detail": "", "url": ""}

    cid = channel_id_of(channel_url)
    handle = handle_of(channel_url)
    if cid:
        params = {"part": "snippet", "id": cid, "key": api_key}
        asked = f"id={cid}"
    elif handle:
        params = {"part": "snippet", "forHandle": handle, "key": api_key}
        asked = f"forHandle={handle}"
    else:
        return {"ok": False, "reason": "the input is not a channel URL or handle",
                "detail": channel_url, "url": ""}

    url = API + "?" + urllib.parse.urlencode(params)
    page = fetcher.get(url)
    # The key is in the query string of the request. The URL kept as evidence never is.
    public_url = API + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if k != "key"})

    status = page.get("status", "")
    body = page.get("body") or ""
    if not status.startswith("200"):
        message = ""
        try:
            message = json.loads(body).get("error", {}).get("message", "")
        except Exception:
            message = re.sub(r"\s+", " ", (page.get("text") or ""))[:300]
        return {"ok": False, "reason": NO_ACCESS if "403" in status else f"the API answered {status}",
                "detail": message[:300], "url": public_url, "http_status": status}

    try:
        data = json.loads(body)
    except Exception:
        return {"ok": False, "reason": "the API answered something that is not JSON",
                "detail": body[:200], "url": public_url, "http_status": status}

    items = data.get("items") or []
    if not items:
        return {"ok": False, "reason": "the API knows no channel under this identifier",
                "detail": asked, "url": public_url, "http_status": status}

    snip = items[0].get("snippet", {})
    return {
        "ok": True,
        "url": public_url,
        "http_status": status,
        "channel_id": items[0].get("id", ""),
        "title": snip.get("title", ""),
        "description": snip.get("description", ""),
        "custom_url": snip.get("customUrl", ""),
        "published_at": snip.get("publishedAt", ""),
        "country": snip.get("country", ""),
    }


def identity_finding(result: dict) -> Finding:
    """The channel's own identity, with the API answer as its source."""
    quote = (result.get("title") or "") + " " + (result.get("custom_url") or "")
    quote = quote.strip() or result.get("channel_id", "")
    return Finding(
        kind="channel_identity",
        value=result.get("channel_id", ""),
        requested_url=result["url"],
        quote=quote[:300],
        note=("youtube data api channels.list. This source needs a key, so it cannot be "
              "re-read anonymously: it is evidence observed at the run, kept with the run, "
              "and it is not revalidated the way a public page is"),
    )


URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+")


def sites_in_description(description: str) -> list[str]:
    """URLs the creator typed into their own channel description.

    This is the only route the documented API leaves open to a creator's own website, and it
    is a weak one: many channels put their links in the panel the API does not expose. When
    it finds nothing, the answer is that it found nothing.
    """
    out: list[str] = []
    for m in URL_IN_TEXT.finditer(description or ""):
        u = m.group(0).rstrip(".,);")
        if u not in out:
            out.append(u)
    return out
