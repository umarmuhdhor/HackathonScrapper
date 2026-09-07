"""Major League Hacking adapter.

mlh.io is an Inertia.js app: the whole season schedule ships as JSON inside
`<script data-page="app" type="application/json">`, so we lift the props out of
that tag instead of parsing markup. Season pages live at
`mlh.io/seasons/<year>/events`; a season starts in August of the previous
calendar year, so the current season is picked from today's date.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from ..models import Hackathon, humanize_deadline, iso, parse_iso, utcnow

NAME = "mlh"
SEASON_URL = "https://mlh.io/seasons/{season}/events"
EVENT_URL = "https://mlh.io/events/{slug}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

PAGE_RE = re.compile(
    r'<script[^>]*data-page="app"[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.S,
)

FORMATS = {"digital": "online", "physical": "onsite", "hybrid": "hybrid"}


def current_season(now: datetime | None = None) -> int:
    """MLH season N runs Aug (N-1) through Jul N."""
    now = now or utcnow()
    return now.year + 1 if now.month >= 8 else now.year


def page_props(html: str) -> dict:
    match = PAGE_RE.search(html)
    if not match:
        raise ValueError("mlh.io: data-page payload not found (markup changed?)")
    return json.loads(match.group(1)).get("props", {})


def _clean_location(raw: str | None) -> str | None:
    """MLH ships half-filled strings like 'Atlanta, , GA' — drop the empty parts."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return ", ".join(parts) or None


def normalize(raw: dict) -> Hackathon:
    slug = raw.get("slug") or raw.get("id")
    start = parse_iso(raw.get("startsAt"))
    end = parse_iso(raw.get("endsAt"))

    status = "ended" if raw.get("status") == "ended" else Hackathon.derive_status(start, end)

    custom = raw.get("customFields") or {}
    themes = [raw["region"]] if raw.get("region") else []
    for key in ("hackathon_focus", "underserved_types"):
        themes += [v for v in (custom.get(key) or []) if v]

    venue = raw.get("venueAddress") or {}
    location = _clean_location(
        raw.get("location")
        or ", ".join(v for v in (venue.get("city"), venue.get("country")) if v)
    )

    return Hackathon(
        id=f"{NAME}:{slug}",
        source=NAME,
        native_id=str(slug),
        title=(raw.get("name") or "").strip(),
        # websiteUrl is the hackathon's own site — where you actually register.
        url=raw.get("websiteUrl") or EVENT_URL.format(slug=slug),
        status=status,
        mode=FORMATS.get(raw.get("formatType")),
        location=location,
        organizer=None,  # MLH is the league, not the organizer; not in the payload
        description=None,
        thumbnail=raw.get("logoUrl") or raw.get("backgroundUrl"),
        start_at=iso(start),
        end_at=iso(end),
        deadline_text=humanize_deadline(end),
        prize_amount=None,      # not published in the season feed
        prize_currency=None,
        participants=None,      # not published in the season feed
        themes=themes,
        # MLH is the one source with a structured country code.
        country=(venue.get("country") or None) if raw.get("formatType") != "digital" else None,
    )


def fetch(seasons: tuple[int, ...] | None = None, timeout: float = 25.0, **_ignored) -> list[Hackathon]:
    seasons = seasons or (current_season(),)
    seen: dict[str, Hackathon] = {}
    with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
        for season in seasons:
            resp = client.get(SEASON_URL.format(season=season))
            resp.raise_for_status()
            props = page_props(resp.text)
            for raw in (props.get("upcomingEvents") or []) + (props.get("pastEvents") or []):
                h = normalize(raw)
                if h.title and h.url:
                    seen[h.id] = h
    return list(seen.values())
