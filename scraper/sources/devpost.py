"""Devpost adapter — uses the public JSON feed behind devpost.com/hackathons."""

from __future__ import annotations

import httpx

from ..models import (
    Hackathon,
    iso,
    parse_money,
    parse_period,
    parse_relative_deadline,
    utcnow,
)

NAME = "devpost"
API = "https://devpost.com/api/hackathons"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "hackathon-screening/0.1 (+personal dashboard)",
}
PER_PAGE = 9  # fixed server-side


def _mode(location: str | None) -> str | None:
    if not location:
        return None
    low = location.lower()
    if "online" in low:
        return "online"
    if "hybrid" in low:
        return "hybrid"
    return "onsite"


def normalize(raw: dict) -> Hackathon:
    native_id = str(raw.get("id"))
    start, end = parse_period(raw.get("submission_period_dates"))
    if end is None:
        end = parse_relative_deadline(raw.get("time_left_to_submission"))
    amount, currency = parse_money(raw.get("prize_amount"))
    location = (raw.get("displayed_location") or {}).get("location")
    thumb = raw.get("thumbnail_url") or None
    if thumb and thumb.startswith("//"):
        thumb = "https:" + thumb

    open_state = (raw.get("open_state") or "").lower()
    status = {"open": "open", "upcoming": "upcoming", "ended": "ended"}.get(
        open_state, Hackathon.derive_status(start, end)
    )
    if status == "open" and end and end < utcnow():
        status = "ended"

    return Hackathon(
        id=f"{NAME}:{native_id}",
        source=NAME,
        native_id=native_id,
        title=raw.get("title", "").strip(),
        url=raw.get("url", ""),
        status=status,
        mode=_mode(location),
        location=location,
        organizer=raw.get("organization_name"),
        description=None,  # not in the list payload; detail page would be needed
        thumbnail=thumb,
        start_at=iso(start),
        end_at=iso(end),
        deadline_text=raw.get("time_left_to_submission"),
        prize_amount=amount,
        prize_currency=currency,
        participants=raw.get("registrations_count"),
        themes=[t.get("name") for t in raw.get("themes") or [] if t.get("name")],
        invite_only=1 if raw.get("invite_only") else 0,
        eligibility_note=raw.get("eligibility_requirement_invite_only_description"),
    )


def fetch(max_pages: int = 8, statuses: tuple[str, ...] = ("open", "upcoming"), timeout: float = 20.0) -> list[Hackathon]:
    """Page through the feed for the requested open states."""
    seen: dict[str, Hackathon] = {}
    with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
        for status in statuses:
            for page in range(1, max_pages + 1):
                params = {"page": page, "status[]": status, "order_by": "deadline"}
                resp = client.get(API, params=params)
                resp.raise_for_status()
                payload = resp.json()
                batch = payload.get("hackathons") or []
                for raw in batch:
                    h = normalize(raw)
                    if h.title and h.url:
                        seen[h.id] = h
                total = (payload.get("meta") or {}).get("total_count", 0)
                if len(batch) < PER_PAGE or page * PER_PAGE >= total:
                    break
    return list(seen.values())
