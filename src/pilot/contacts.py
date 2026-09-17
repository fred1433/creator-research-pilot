"""Finding a published professional contact, and refusing the ones that are not published.

The rule this file enforces: an address counts as published when it appears on a property
whose relation to the channel has been established, and the record always says which
property and which relation. Everything else is refused by name, with the reason kept:

  * an address on a site whose relation to the channel is unconfirmed. This is the fan page,
    the press article and the merch reseller. The address may well be real; nothing ties it
    to this channel.
  * an address on a link aggregator. Anyone can put anything on one, so the aggregator
    published it, not the creator.
  * an address behind an access mechanism. A creator who puts an address behind "prove you
    are human" has published it to humans who ask, not to this workflow.

The last category is where a large part of the honest "not found" results come from, and
refusing to go around it is the difference between a research tool and a scraper.

A professional contact form is kept, as a contact of kind `form`. A reviewer can use one, so
calling a channel "not found" when it publishes a working contact route would be wrong in
the direction that matters: it would hide something usable.

An address that belongs to a manager, a label or an agency is not refused either. The buyer
allows a representative; the work is to label it as one, because a human writes to an agent
differently. Presenting it silently as the creator's own address would be the lie.
"""

from __future__ import annotations

import re
import urllib.parse

from .model import Finding

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

AGGREGATOR_HOSTS = ("linktr.ee", "bio.site", "beacons.ai", "linkin.bio", "koji.to",
                    "lnk.bio", "solo.to", "campsite.bio", "carrd.co", "milkshake.app")
SOCIAL_HOSTS = ("instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com",
                "fb.com", "youtube.com", "youtu.be", "twitch.tv", "spotify.com",
                "apple.com", "snapchat.com", "pinterest.com", "threads.net", "reddit.com",
                "discord.gg", "discord.com", "linkedin.com", "soundcloud.com", "patreon.com")

# Addresses that are never a person and never a professional contact.
JUNK_LOCAL = ("noreply", "no-reply", "donotreply", "postmaster", "abuse", "webmaster",
              "sentry", "wordpress", "root@")
JUNK_DOMAIN = ("sentry.io", "domain.com", "email.com", "yourdomain.com", "wixpress.com",
               "squarespace.com", "shopify.com", "godaddy.com", "sentry.wixpress.com",
               "schema.org", "w3.org")

REFUSED_UNCONFIRMED_SITE = ("refused: the address sits on a site whose relation to this "
                            "channel is not established")
REFUSED_AGGREGATOR = ("refused: the address sits on a link aggregator, which publishes on "
                      "anyone's behalf")
REFUSED_ACCESS_MECHANISM = ("not collected: the channel's professional address sits behind an "
                            "access mechanism, which is a choice to publish to humans who ask")
REFUSED_NO_CONTEXT = "refused: no quotable context around the address on the page"
REFUSED_JUNK = "refused: not a contactable mailbox"

CONTACT_HINTS = ("contact", "get-in-touch", "getintouch", "enquir", "press", "media",
                 "about", "work-with-us", "partnership", "business", "booking")


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def is_aggregator(url: str) -> bool:
    h = host_of(url)
    return any(h == a or h.endswith("." + a) for a in AGGREGATOR_HOSTS)


def is_social(url: str) -> bool:
    h = host_of(url)
    return any(h == s or h.endswith("." + s) for s in SOCIAL_HOSTS)


def looks_junk(addr: str) -> bool:
    low = addr.lower()
    local, _, dom = low.partition("@")
    if any(local.startswith(j) for j in JUNK_LOCAL):
        return True
    if any(dom == d or dom.endswith("." + d) for d in JUNK_DOMAIN):
        return True
    # Image and asset filenames routinely parse as addresses.
    return bool(re.search(r"\.(png|jpg|jpeg|gif|svg|webp|css|js)$", dom))


def emails_in(text: str) -> list[str]:
    seen, out = set(), []
    for m in EMAIL_RE.finditer(text or ""):
        a = m.group(0).strip(".,;:)]}>\"'")
        if looks_junk(a) or a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append(a)
    return out


def quote_around(text: str, needle: str, width: int = 90) -> str:
    """A quote a reader can find on the page, centred on the address.

    Trimmed to whole words so the citation reads like something a person copied, and capped
    so it survives the re-render the guard performs.
    """
    i = (text or "").find(needle)
    if i < 0:
        return ""
    start, end = max(0, i - width // 2), min(len(text), i + len(needle) + width // 2)
    q = text[start:end].strip()
    if start > 0 and " " in q:
        q = q.split(" ", 1)[1]
    if end < len(text) and " " in q:
        q = q.rsplit(" ", 1)[0]
    return q.strip()


def context_around(text: str, needle: str, width: int = 320) -> str:
    """The sentence the address lives in, for the human who has to judge the page."""
    i = (text or "").find(needle)
    if i < 0:
        return ""
    return (text[max(0, i - width // 2): i + len(needle) + width // 2]).strip()


def addresses_on_page(page: dict, relation_established: bool) -> tuple[list[Finding], list[dict]]:
    """Every address on one page, split into proposals and refusals with their reasons.

    Nothing here is believed: a proposal still has to survive the guard in `verify.py`.
    """
    kept: list[Finding] = []
    refused: list[dict] = []
    text = page.get("text") or ""
    body = page.get("body") or ""
    url = page.get("final_url") or page.get("url") or ""

    mailtos = [urllib.parse.unquote(a) for a in re.findall(r'mailto:([^"\'?\s>&]+)', body)]
    seen: set[str] = set()
    for addr in mailtos + emails_in(text):
        low = addr.lower()
        if low in seen or looks_junk(addr):
            continue
        seen.add(low)
        if is_aggregator(url):
            refused.append({"value": addr, "source_url": url, "reason": REFUSED_AGGREGATOR})
            continue
        if not relation_established:
            refused.append({"value": addr, "source_url": url, "reason": REFUSED_UNCONFIRMED_SITE})
            continue
        q = quote_around(text, addr)
        if len(q) < 8:
            # Very often the address exists only inside a link. The quote then comes from the
            # markup, which the guard checks against the markup, and the record says so.
            q = quote_around(re.sub(r"\s+", " ", body), addr, width=60)
        if len(q) < 8:
            refused.append({"value": addr, "source_url": url, "reason": REFUSED_NO_CONTEXT})
            continue
        kept.append(Finding(
            kind="contact_email",
            value=addr,
            requested_url=page.get("url") or url,
            final_url=url,
            quote=q[:300],
            context=context_around(text, addr),
            note="published on a property whose relation to the channel is established",
        ))
    return kept, refused


def contact_form_on_page(page: dict) -> Finding | None:
    """A professional contact form is a usable route, so it is recorded rather than ignored."""
    body = page.get("body") or ""
    text = page.get("text") or ""
    url = page.get("final_url") or page.get("url") or ""
    if "<form" not in body.lower():
        return None
    low = text.lower()
    hit = next((h for h in ("contact", "get in touch", "enquiry", "enquiries", "work with")
                if h in low), "")
    if not hit:
        return None
    q = quote_around(text, hit, width=120) or hit
    if len(q) < 8:
        return None
    return Finding(kind="contact_form", value=url, requested_url=page.get("url") or url,
                   final_url=url, quote=q[:300], context=context_around(text, hit),
                   note="a professional contact form, usable by a human reviewer")


def contact_pages(home: dict, limit: int = 2) -> list[str]:
    """Up to two pages a human would click to find a professional address. No crawling."""
    body = home.get("body") or ""
    base = home.get("final_url") or home.get("url") or ""
    out: list[str] = []
    for href in re.findall(r'href=["\']([^"\'>#]+)["\']', body):
        low = href.lower()
        if not any(h in low for h in CONTACT_HINTS):
            continue
        if low.startswith(("mailto:", "tel:", "javascript:")):
            continue
        u = urllib.parse.urljoin(base, href)
        if host_of(u) != host_of(base):
            continue
        if u.rstrip("/") == base.rstrip("/") or u in out:
            continue
        out.append(u)
        if len(out) >= limit:
            break
    return out


def candidate_sites(urls: list[str]) -> list[str]:
    """The published links that could be the creator's own site, best first."""
    out = []
    for u in urls:
        u = (u or "").strip()
        if not u.startswith("http") or is_social(u) or "@" in u:
            continue
        if u not in out:
            out.append(u)
    # An aggregator is not a property of the creator, but it is often the only link a channel
    # publishes. It is kept last so the real site wins when there is one, and any address on
    # it is refused by name further down.
    return sorted(out, key=is_aggregator)
