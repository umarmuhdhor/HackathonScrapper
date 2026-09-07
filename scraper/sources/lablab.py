"""lablab.ai adapter.

lablab.ai is a Next.js app-router site: the hackathon list is not in the served
HTML markup, it is streamed as RSC flight payload inside
`self.__next_f.push([1, "..."])` chunks. We rebuild that payload and pull the
event objects straight out of it — no HTML parsing, no headless browser.
"""

from __future__ import annotations

import json
import re

import httpx

from ..models import Hackathon, humanize_deadline, iso, parse_iso

NAME = "lablab"
LIST_URL = "https://lablab.ai/ai-hackathons"
EVENT_URL = "https://lablab.ai/event/{slug}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')
OBJ_START_RE = re.compile(r'\{"id":"')
DECODER = json.JSONDecoder()
PRIZE_RE = re.compile(r"\$\s?([\d][\d,.]*)\s*(k|m)?", re.I)


def flight_payload(html: str) -> str:
    """Concatenate and unescape every RSC flight chunk in the page."""
    parts = []
    for match in FLIGHT_RE.finditer(html):
        try:
            parts.append(json.loads(f'"{match.group(1)}"'))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def extract_events(payload: str) -> list[dict]:
    """Scan the flight payload for embedded event objects."""
    events: dict[str, dict] = {}
    for match in OBJ_START_RE.finditer(payload):
        try:
            obj, _ = DECODER.raw_decode(payload, match.start())
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if not obj.get("slug") or not obj.get("name"):
            continue
        if obj.get("type") and obj["type"] != "HACKATHON":
            continue
        if not (obj.get("startAt") or obj.get("endAt")):
            continue
        events[obj["slug"]] = obj
    return list(events.values())


def parse_prize(text: str | None) -> tuple[int | None, str | None]:
    """Best-effort prize pool from the free-text description ('$6,666', '$25k')."""
    if not text:
        return None, None
    best = 0
    for amount, suffix in PRIZE_RE.findall(text):
        try:
            value = float(amount.replace(",", ""))
        except ValueError:
            continue
        if suffix and suffix.lower() == "k":
            value *= 1_000
        elif suffix and suffix.lower() == "m":
            value *= 1_000_000
        best = max(best, int(value))
    return (best or None), ("USD" if best else None)


def _mode(event_type: str | None) -> str | None:
    if not event_type:
        return None
    low = event_type.lower()
    if "online" in low:
        return "online"
    if "hybrid" in low:
        return "hybrid"
    if "onsite" in low or "offline" in low or "in_person" in low:
        return "onsite"
    return low


def normalize(raw: dict) -> Hackathon:
    slug = raw["slug"]
    start = parse_iso(raw.get("startAt"))
    end = parse_iso(raw.get("endAt"))
    description = (raw.get("description") or "").strip() or None
    amount, currency = parse_prize(description)
    counts = raw.get("_count") or {}
    techs = raw.get("technologyList") or raw.get("techs") or []
    themes = [
        t.get("name") or t.get("slug")
        for t in techs
        if isinstance(t, dict) and (t.get("name") or t.get("slug"))
    ]

    return Hackathon(
        id=f"{NAME}:{slug}",
        source=NAME,
        native_id=slug,
        title=raw.get("name", "").strip(),
        url=EVENT_URL.format(slug=slug),
        status=Hackathon.derive_status(start, end),
        mode=_mode(raw.get("eventType")),
        location=raw.get("location") or raw.get("eventType"),
        organizer=raw.get("organizerName") or "lablab.ai",
        description=description[:2000] if description else None,
        thumbnail=raw.get("thumbnailLink") or raw.get("imageLink"),
        start_at=iso(start),
        end_at=iso(end),
        deadline_text=humanize_deadline(end),
        prize_amount=amount,
        prize_currency=currency,
        participants=counts.get("participants"),
        themes=themes,
        signup_open=1 if raw.get("signupActive") else 0,
    )


def fetch(timeout: float = 25.0, **_ignored) -> list[Hackathon]:
    with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
        resp = client.get(LIST_URL)
        resp.raise_for_status()
        html = resp.text

    events = extract_events(flight_payload(html))
    out = []
    for raw in events:
        h = normalize(raw)
        if h.title:
            out.append(h)
    return out
