"""Read the eligibility rules off a Devpost hackathon page.

The list feed says nothing about who may enter. The rules page does: sponsored
Devpost hackathons carry a boilerplate section — "The Hackathon IS NOT open to:
Individuals who are residents of ... (including, but not limited to, Argentina,
Australia, ..., Indonesia, ...)" — because prize law blocks whole countries.
That is the single most important eligibility fact for anyone outside the US,
and it exists nowhere in the API, so we fetch and parse it.

Coverage differs sharply by source, and the code refuses to pretend otherwise:

* **Devpost** — one uniform ``/rules`` page per event. Reliable.
* **MLH** — links out to each event's own site. Roughly half are client-rendered
  single-page apps that serve almost no text to a plain HTTP fetch, so those are
  recorded as *unread*, not as *clean*.
* **lablab.ai** — event pages are RSC-rendered and carry no rules text at all.
  What does exist is the organiser's own description, already stored during
  scraping, which sometimes states the event is global. That is a claim, not a
  rules page, so it never produces a ✓ on its own.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time

import httpx

from .models import iso, utcnow

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Countries that actually turn up in Devpost prize-law exclusion lists, plus the
# common host countries. Longest names first so "United Arab Emirates" wins over
# a bare "Arab".
COUNTRIES = {
    "united arab emirates": "AE", "united states of america": "US", "united states": "US",
    "united kingdom": "GB", "north korea": "KP", "south korea": "KR", "south africa": "ZA",
    "new zealand": "NZ", "saudi arabia": "SA", "sri lanka": "LK", "hong kong": "HK",
    "puerto rico": "PR", "costa rica": "CR", "el salvador": "SV", "czech republic": "CZ",
    "dominican republic": "DO", "argentina": "AR", "australia": "AU", "indonesia": "ID",
    "singapore": "SG", "malaysia": "MY", "philippines": "PH", "thailand": "TH",
    "vietnam": "VN", "belarus": "BY", "russia": "RU", "cuba": "CU",
    "iran": "IR", "syria": "SY", "ukraine": "UA", "venezuela": "VE", "myanmar": "MM",
    "sudan": "SD", "zimbabwe": "ZW", "italy": "IT", "brazil": "BR",
    "canada": "CA", "mexico": "MX", "india": "IN", "germany": "DE", "france": "FR",
    "spain": "ES", "japan": "JP", "china": "CN", "taiwan": "TW", "netherlands": "NL",
    "belgium": "BE", "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "poland": "PL", "portugal": "PT", "ireland": "IE", "austria": "AT", "greece": "GR",
    "turkey": "TR", "israel": "IL", "egypt": "EG", "nigeria": "NG", "kenya": "KE",
    "ghana": "GH", "pakistan": "PK", "bangladesh": "BD", "nepal": "NP", "colombia": "CO",
    "chile": "CL", "peru": "PE", "switzerland": "CH", "romania": "RO", "hungary": "HU",
    # Sanctions-list names that appear verbatim in Devpost's own boilerplate but
    # were missing here, so they were silently dropped from exclusion lists.
    "western sahara": "EH", "afghanistan": "AF", "kazakhstan": "KZ", "somalia": "SO",
    "djibouti": "DJ", "iraq": "IQ", "libya": "LY", "yemen": "YE", "lebanon": "LB",
}
_COUNTRY_RE = re.compile(
    "|".join(rf"\b{re.escape(n)}\b" for n in sorted(COUNTRIES, key=len, reverse=True)),
    re.I,
)

# The residency phrase itself is the anchor: the country list always follows it
# directly. Anchoring on "not open to" instead used to start the scan hundreds of
# characters too early, which both invented bans (Devpost's "at least twenty years
# old in Taiwan" age clause became a Taiwan ban) and cut the real list short.
RESIDENCY_ANCHOR_RE = re.compile(
    r"residents?\s+of|resident\s+of|domiciled\s+in|reside\s+in", re.I
)
# The anchor only counts when the sentence around it is phrased as an exclusion.
# Without this, "participants who are residents of X may enter" would ban X.
NEGATIVE_LEAD_RE = re.compile(
    r"not\s+be\s+an?|not\s+be\s+a|not\s+open\s+to|are\s+not\s+eligible|is\s+not\s+eligible"
    r"|ineligible|excluded|may\s+not\s+(?:enter|participate)|prohibited|void\s+in",
    re.I,
)
# How far back to look for that negative phrasing, and how much of the list to read.
LEAD_WINDOW = 160
CLAUSE_WINDOW = 1400
# Stop before the next numbered sub-clause or ALL-CAPS section heading, so one
# clause's country list cannot bleed into the next clause's text.
CLAUSE_END_RE = re.compile(r"\(\d+\)\s*(?!not)|\d+\.\s+[A-Z]{3,}|;\s*(?:and|or)\s*\(")

STUDENT_ONLY_RE = re.compile(
    r"(?:must\s+be|only\s+open\s+to|open\s+only\s+to)[^.]{0,80}"
    r"(?:currently\s+enrolled|full[- ]time\s+student|registered\s+student|university\s+student)",
    re.I,
)

# Below this much extracted text the page is a JavaScript shell, not content —
# parsing it would "find no restrictions" simply because it found no words.
MIN_PAGE_TEXT = 1200

# A page that is a JavaScript shell today will still be one tomorrow. Retrying
# every failure on every daily run would hammer those sites for nothing, so a
# failed read waits this long before it is attempted again.
RETRY_FAILED_AFTER_DAYS = 7

# Length alone is not enough: a marketing page full of prose also clears it, and
# would then be recorded as "rules read, nothing found" — a false green light.
# The page must actually look like it discusses who may enter.
#
# Every phrase here has to be about *entry*, not about the project. "Requirements"
# and "must be" used to be in this list and were the whole reason three real
# Devpost pages ("Project and Submission Requirements", "Python Requirement")
# were recorded as verified rules pages when they say nothing about eligibility.
RULES_MARKER_RE = re.compile(
    r"eligib|official\s+rules|terms\s+(?:and|&)\s+conditions|not\s+open\s+to"
    r"|who\s+can\s+(?:participate|enter|join)"
    r"|age\s+of\s+majority|residents?\s+of",
    re.I,
)

# The organiser saying the event is worldwide. Weaker than a rules page, so it
# only ever softens a warning; it never clears one.
OPEN_GLOBAL_RE = re.compile(
    r"(?:open\s+to\s+(?:everyone|anyone|all)"
    r"|anywhere\s+in\s+the\s+world"
    r"|from\s+anywhere"
    r"|world[- ]?wide"
    r"|globally\s+open"
    r"|participants?\s+from\s+(?:all\s+over\s+the\s+world|any\s+country)"
    r"|no\s+(?:geographic|country|regional)\s+restrictions?)",
    re.I,
)

# Where to look for rules, per source. Devpost has a dedicated page; MLH events
# are third-party sites where the path is anyone's guess.
CANDIDATE_PATHS = {
    "devpost": ("/rules", ""),
    "mlh": ("", "/rules", "/legal", "/terms", "/faq"),
    "lablab": ("",),
}

# These boilerplate phrases name a country as the *governing law* or as a
# sub-national carve-out, not as an excluded country. Left in, every sponsored
# hackathon would look closed to the whole United States and all of Canada.
NOISE_RE = re.compile(
    r"laws?\s+of\s+the\s+United\s+States"
    r"|United\s+States\s+Treasury[^,)]*"
    r"|Office\s+of\s+Foreign\s+Assets\s+Control"
    r"|U\.?S\.?\s+(?:law|Treasury|government|export)[^,)]*"
    r"|\(?Province\s+of\)?\s*Quebec"
    r"|Quebec"
    r"|Crimea"
    r"|Donetsk[^,)]*"
    r"|Luhansk[^,)]*",
    re.I,
)


def page_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def find_openness_claim(text: str) -> str | None:
    """The organiser's own 'anyone, anywhere' line, quoted back verbatim."""
    match = OPEN_GLOBAL_RE.search(text)
    if not match:
        return None
    start = max(0, match.start() - 90)
    return text[start: match.end() + 90].strip()


def parse_eligibility(text: str) -> dict:
    """Pull excluded countries and a student requirement out of rules prose.

    Only countries that follow a residency phrase inside a negatively-framed
    sentence count. Countries named anywhere else on the page — sponsor
    addresses, age-of-majority carve-outs, employee clauses — are ignored.
    """
    excluded: dict[str, None] = {}
    snippet = None

    for match in RESIDENCY_ANCHOR_RE.finditer(text):
        lead = text[max(0, match.start() - LEAD_WINDOW): match.start()]
        if not NEGATIVE_LEAD_RE.search(lead):
            continue
        clause = text[match.start(): match.start() + CLAUSE_WINDOW]
        # Skip the anchor itself before hunting for the clause boundary, so
        # "residents of" cannot terminate its own clause.
        cut = CLAUSE_END_RE.search(clause[40:])
        if cut:
            clause = clause[: cut.start() + 40]
        clean = NOISE_RE.sub(" ", clause)
        found = [COUNTRIES[m.group(0).lower()] for m in _COUNTRY_RE.finditer(clean)]
        if not found:
            continue
        for code in found:
            excluded.setdefault(code, None)
        if snippet is None:
            snippet = clause[:600].strip()

    return {
        "excluded_countries": list(excluded),
        "requires_student": bool(STUDENT_ONLY_RE.search(text)),
        "snippet": snippet,
        "openness_claim": find_openness_claim(text),
    }


def fetch_rules(url: str, client: httpx.Client, source: str = "devpost") -> dict | None:
    """Try each candidate path for this source.

    Returns None when nothing substantive could be read — "unknown", which must
    never be recorded the same way as "read it and found nothing".
    """
    base = url.rstrip("/")
    last: dict | None = None
    for path in CANDIDATE_PATHS.get(source, ("",)):
        candidate = f"{base}{path}"
        try:
            resp = client.get(candidate)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        text = page_text(resp.text)
        if len(text) < MIN_PAGE_TEXT or not RULES_MARKER_RE.search(text):
            # A JS shell, or a page that never discusses eligibility at all.
            continue
        result = parse_eligibility(text)
        result["source_url"] = candidate
        if result["excluded_countries"] or result["requires_student"]:
            return result
        last = result
    return last


def enrich(conn, limit: int = 200, force: bool = False, delay: float = 0.4,
           sources: tuple[str, ...] = ("devpost",), progress=None) -> dict:
    """Fetch and store rules for rows of the given sources that need checking."""
    placeholders = ",".join("?" * len(sources))
    where = f"source IN ({placeholders}) AND status IN ('open','upcoming')"
    args: list = list(sources)
    if not force:
        where += (
            " AND (rules_checked_at IS NULL"
            f" OR (rules_ok = 0 AND rules_checked_at < datetime('now', '-{RETRY_FAILED_AFTER_DAYS} days')))"
        )
    args.append(limit)
    rows = conn.execute(
        f"SELECT id, url, title, source, description FROM hackathons WHERE {where}"
        " ORDER BY end_at LIMIT ?",
        args,
    ).fetchall()

    checked = restricted = failed = 0
    with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as client:
        for i, row in enumerate(rows):
            result = None
            try:
                result = fetch_rules(row["url"], client, row["source"])
            except Exception:
                result = None

            # lablab event pages are RSC shells, but the description we already
            # scraped sometimes says the event is worldwide. Use it as a claim.
            if result is None and row["description"]:
                claim = find_openness_claim(row["description"])
                if claim:
                    result = {"excluded_countries": [], "requires_student": False,
                              "snippet": None, "openness_claim": claim,
                              "from_description": True}
            if result is None:
                # Stamp the attempt so we do not retry forever, but flag it as
                # unread so eligibility keeps saying "belum pasti".
                failed += 1
                conn.execute(
                    "UPDATE hackathons SET rules_checked_at = ?, rules_ok = 0 WHERE id = ?",
                    (iso(utcnow()), row["id"]),
                )
            else:
                checked += 1
                if result["excluded_countries"]:
                    restricted += 1
                # A description claim is not a rules page: store the claim but
                # leave rules_ok at 0 so the verdict stays "perlu dicek".
                verified = 0 if result.get("from_description") else 1
                conn.execute(
                    "UPDATE hackathons SET excluded_countries = ?, requires_student = ?,"
                    " eligibility_snippet = ?, openness_claim = ?, rules_checked_at = ?,"
                    " rules_ok = ? WHERE id = ?",
                    (
                        json.dumps(result["excluded_countries"]),
                        1 if result["requires_student"] else 0,
                        result["snippet"],
                        result.get("openness_claim"),
                        iso(utcnow()),
                        verified,
                        row["id"],
                    ),
                )
            conn.commit()
            if progress:
                progress(i + 1, len(rows), row["title"])
            time.sleep(delay)

    return {"total": len(rows), "checked": checked, "restricted": restricted, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .db import connect

    parser = argparse.ArgumentParser(
        description="Baca halaman aturan Devpost untuk larangan negara & syarat mahasiswa."
    )
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--source", nargs="+", default=["devpost"],
                        choices=["devpost", "lablab", "mlh"],
                        help="sumber yang halaman aturannya dibaca")
    parser.add_argument("--force", action="store_true", help="periksa ulang yang sudah dicek")
    parser.add_argument("--delay", type=float, default=0.4, help="jeda antar permintaan (detik)")
    args = parser.parse_args(argv)

    conn = connect()
    result = enrich(
        conn, limit=args.limit, force=args.force, delay=args.delay,
        sources=tuple(args.source),
        progress=lambda i, n, t: print(f"  {i}/{n} {t[:50]}", flush=True),
    )
    print(f"  selesai: {result['checked']} terbaca, {result['restricted']} punya larangan negara,"
          f" {result['failed']} gagal")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
