"""Normalized hackathon record shared by every source adapter."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ],
        start=1,
    )
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_money(raw: str | None) -> tuple[int | None, str | None]:
    """'$<span data-currency-value>740,000</span>' -> (740000, 'USD')."""
    if not raw:
        return None, None
    text = re.sub(r"<[^>]+>", "", raw)
    currency = "USD" if "$" in text else ("EUR" if "€" in text else None)
    digits = re.sub(r"[^\d]", "", text)
    return (int(digits) if digits else None), currency


def parse_relative_deadline(raw: str | None, now: datetime | None = None) -> datetime | None:
    """'25 days left' / 'about 9 hours left' -> absolute UTC datetime."""
    if not raw:
        return None
    now = now or utcnow()
    m = re.search(r"(\d+)\s*(minute|hour|day|month)", raw, re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = {
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "month": timedelta(days=30 * n),
    }[unit]
    return now + delta


def humanize_deadline(end: datetime | None, now: datetime | None = None) -> str | None:
    """Absolute end date -> '12 days left' / 'about 5 hours left' / 'closed'."""
    if not end:
        return None
    now = now or utcnow()
    delta = end - now
    seconds = delta.total_seconds()
    if seconds <= 0:
        return "closed"
    if delta.days >= 1:
        return f"{delta.days} days left"
    return f"about {int(seconds // 3600)} hours left"


def parse_period(raw: str | None) -> tuple[datetime | None, datetime | None]:
    """Devpost period strings -> (start, end).

    Handles 'Jul 31 - Oct 01, 2026', 'Sep 04 - 06, 2026',
    'Dec 15, 2025 - Jan 10, 2026'.
    """
    if not raw:
        return None, None
    parts = [p.strip() for p in re.split(r"\s+-\s+|\s+–\s+", raw, maxsplit=1)]
    if len(parts) == 1:
        parts = parts * 2  # single-day event, e.g. 'Sep 09, 2026'
    if len(parts) != 2:
        return None, None
    left, right = parts

    def tokens(chunk: str) -> tuple[int | None, int | None, int | None]:
        mon = re.search(r"([A-Za-z]{3})", chunk)
        day = re.search(r"\b(\d{1,2})\b", chunk)
        year = re.search(r"\b(20\d{2})\b", chunk)
        return (
            MONTHS.get(mon.group(1).lower()) if mon else None,
            int(day.group(1)) if day else None,
            int(year.group(1)) if year else None,
        )

    lm, ld, ly = tokens(left)
    rm, rd, ry = tokens(right)
    ry = ry or ly
    ly = ly or ry
    rm = rm or lm
    lm = lm or rm
    if not (lm and ld and ly and rm and rd and ry):
        return None, None
    # 'Dec 15 - Jan 10, 2026': start rolls back a year.
    if lm > rm and ly == ry:
        ly -= 1
    try:
        start = datetime(ly, lm, ld, tzinfo=timezone.utc)
        end = datetime(ry, rm, rd, 23, 59, 59, tzinfo=timezone.utc)
    except ValueError:
        return None, None
    return start, end




# Country resolution for onsite events. The sources are inconsistent: MLH gives a
# proper ISO-2 code, Devpost gives free text that is often just a venue name. When
# nothing resolves we leave it None — eligibility then says "cek sendiri" instead
# of pretending to know.
_US_STATES = (
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia "
    "hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts "
    "michigan minnesota mississippi missouri montana nebraska nevada ohio oklahoma oregon "
    "pennsylvania tennessee texas utah vermont virginia washington wisconsin wyoming"
).split()
_US_ABBR = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
_CA_HINTS = {"ontario", "quebec", "alberta", "manitoba", "saskatchewan", "nova scotia",
             "british columbia", "toronto", "ottawa", "waterloo", "montreal", "vancouver", "on", "bc", "qc", "ab"}
_IN_HINTS = {"india", "bengaluru", "bangalore", "delhi", "new delhi", "mumbai", "hyderabad",
             "chennai", "kolkata", "pune", "jaipur", "agra", "bhopal", "noida", "gurugram", "ahmedabad"}
_ID_HINTS = {"indonesia", "jakarta", "bandung", "surabaya", "yogyakarta", "depok", "tangerang", "bekasi"}
_COUNTRY_NAMES = {
    "usa": "US", "u.s.a": "US", "united states": "US", "america": "US",
    "canada": "CA", "india": "IN", "indonesia": "ID", "germany": "DE", "deutschland": "DE",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "france": "FR", "spain": "ES", "netherlands": "NL", "singapore": "SG", "malaysia": "MY",
    "australia": "AU", "japan": "JP", "china": "CN", "brazil": "BR", "mexico": "MX",
    "nigeria": "NG", "kenya": "KE", "bhutan": "BT", "bangladesh": "BD", "pakistan": "PK",
    "philippines": "PH", "vietnam": "VN", "thailand": "TH", "switzerland": "CH", "italy": "IT",
    "poland": "PL", "sweden": "SE", "ireland": "IE", "uae": "AE", "united arab emirates": "AE",
}


def resolve_country(location: str | None) -> str | None:
    """Best-effort ISO-2 country code from a free-text location string."""
    if not location:
        return None
    text = location.lower().strip()
    if text in {"online", "virtual", "everywhere, worldwide", "worldwide", "remote"}:
        return None
    for name, code in _COUNTRY_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            return code
    parts = [p.strip() for p in re.split(r"[,\u2014\u2013-]", location) if p.strip()]
    for part in parts:
        if part.upper() in _US_ABBR and len(part) == 2:
            return "US"
    for hint_set, code in ((_ID_HINTS, "ID"), (_IN_HINTS, "IN"), (_CA_HINTS, "CA")):
        if any(re.search(rf"\b{re.escape(h)}\b", text) for h in hint_set):
            return code
    if any(re.search(rf"\b{state}\b", text) for state in _US_STATES):
        return "US"
    return None


# Who a hackathon is aimed at. There is no machine-readable eligibility field on
# any of the three sources, so this is a keyword heuristic over the text we do
# get — good enough to filter by, not a substitute for reading the rules page.
STUDENT_RE = re.compile(
    r"\b(students?|university|universitas|universities|college|collegiate|campus|kampus"
    r"|undergrad(?:uate)?|school|schools|mahasiswa|siswa|pelajar|youth|teens?"
    r"|institute of technology|polytechnic|politeknik)\b",
    re.I,
)
# MLH is the collegiate league, so its member events are student-run by default —
# except its own Global Hack Week, which anyone may join.
MLH_OPEN_RE = re.compile(r"global hack week", re.I)


def classify_audience(source: str, *fields: object) -> str:
    """'student' or 'general', from title/organizer/location/theme text."""
    blob = " ".join(str(f) for f in fields if f)
    if source == "mlh":
        return "general" if MLH_OPEN_RE.search(blob) else "student"
    return "student" if STUDENT_RE.search(blob) else "general"


@dataclass
class Hackathon:
    """One hackathon, source-agnostic. `id` is '<source>:<native_id>'."""

    id: str
    source: str
    native_id: str
    title: str
    url: str
    status: str = "unknown"          # open | upcoming | ended | unknown
    mode: str | None = None          # online | onsite | hybrid
    location: str | None = None
    organizer: str | None = None
    description: str | None = None
    thumbnail: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    deadline_text: str | None = None
    prize_amount: int | None = None
    prize_currency: str | None = None
    participants: int | None = None
    themes: list[str] = field(default_factory=list)
    audience: str | None = None      # student | general — filled in by __post_init__
    country: str | None = None       # ISO-2 for onsite events, None when online/unknown
    invite_only: int | None = None   # 1 when the source says entry is by invitation
    signup_open: int | None = None   # 1/0 when the source exposes registration state
    eligibility_note: str | None = None
    scraped_at: str = field(default_factory=lambda: iso(utcnow()))

    def __post_init__(self) -> None:
        if self.country is None and self.mode in ("onsite", "hybrid"):
            self.country = resolve_country(self.location)
        if not self.audience:
            self.audience = classify_audience(
                self.source, self.title, self.organizer, self.location,
                " ".join(self.themes), self.description,
            )

    def to_row(self) -> dict:
        row = asdict(self)
        row["themes"] = json.dumps(self.themes, ensure_ascii=False)
        return row

    @staticmethod
    def derive_status(start: datetime | None, end: datetime | None, now: datetime | None = None) -> str:
        now = now or utcnow()
        if end and end < now:
            return "ended"
        if start and start > now:
            return "upcoming"
        if start or end:
            return "open"
        return "unknown"
