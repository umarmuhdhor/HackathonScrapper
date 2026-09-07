"""Scrape orchestration + CLI.

`scrape()` is shared by the CLI and the API. Results are always written to
SQLite, and a source that was scraped within the TTL is skipped instead of
re-fetched — the stored rows are the working copy, the network is only for
refreshing them.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from .db import connect, is_fresh, last_success, log_run, upsert_hackathons
from .models import iso, utcnow
from .rules import RETRY_FAILED_AFTER_DAYS, enrich as enrich_rules
from .sources import SOURCES

DEFAULT_TTL_MINUTES = 360  # 6 hours


@dataclass
class ScrapeOptions:
    """Everything the user can narrow a scrape run by."""

    sources: list[str] = field(default_factory=lambda: list(SOURCES))
    force: bool = False
    ttl_minutes: int = DEFAULT_TTL_MINUTES
    max_pages: int = 8
    devpost_statuses: list[str] = field(default_factory=lambda: ["open", "upcoming"])
    mlh_seasons: list[int] | None = None
    # After a scrape, read the rules pages of anything not checked yet. Without
    # this a freshly scraped hackathon has unknown eligibility, which is exactly
    # how a country-restricted event slips into the recommendations.
    auto_rules: bool = True
    rules_budget: int = 40

    def kwargs_for(self, source: str) -> dict:
        if source == "devpost":
            return {"max_pages": self.max_pages, "statuses": tuple(self.devpost_statuses)}
        if source == "mlh":
            return {"seasons": tuple(self.mlh_seasons) if self.mlh_seasons else None}
        return {}


def scrape_source(source: str, conn, options: ScrapeOptions) -> dict:
    """Fetch one source unless its stored data is still fresh."""
    if not options.force and is_fresh(conn, source, options.ttl_minutes):
        return {
            "source": source,
            "skipped": True,
            "reason": "cached",
            "last_success": last_success(conn, source),
            "found": 0, "new": 0, "updated": 0, "error": None,
        }

    started = iso(utcnow())
    try:
        items = SOURCES[source].fetch(**options.kwargs_for(source))
    except Exception as exc:  # network hiccup or markup drift — log, keep going
        message = f"{type(exc).__name__}: {exc}"
        log_run(conn, source, started, 0, 0, 0, error=message)
        return {"source": source, "skipped": False, "error": message,
                "found": 0, "new": 0, "updated": 0}

    inserted, updated = upsert_hackathons(conn, items)
    log_run(conn, source, started, len(items), inserted, updated)
    result = {
        "source": source, "skipped": False, "reason": None,
        "found": len(items), "new": inserted, "updated": updated, "error": None,
    }

    if options.auto_rules:
        pending = conn.execute(
            "SELECT COUNT(*) FROM hackathons WHERE source = ?"
            " AND status IN ('open','upcoming')"
            " AND (rules_checked_at IS NULL OR (rules_ok = 0 AND rules_checked_at <"
            f" datetime('now', '-{RETRY_FAILED_AFTER_DAYS} days')))",
            (source,),
        ).fetchone()[0]
        if pending:
            rules = enrich_rules(conn, limit=options.rules_budget, sources=(source,))
            result["rules"] = rules
            # Say so when the budget could not cover everything, instead of
            # letting unchecked rows look vetted.
            result["rules_pending"] = max(0, pending - rules["total"])
        else:
            result["rules"] = {"total": 0, "checked": 0, "restricted": 0, "failed": 0}
            result["rules_pending"] = 0
    return result


def scrape(options: ScrapeOptions, conn=None) -> list[dict]:
    own_conn = conn is None
    conn = conn or connect()
    try:
        return [scrape_source(name, conn, options) for name in options.sources]
    finally:
        if own_conn:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape live hackathons into the local DB.")
    parser.add_argument("--source", default="all", choices=[*SOURCES, "all"])
    parser.add_argument("--pages", type=int, default=8, help="max pages per source (Devpost)")
    parser.add_argument(
        "--devpost-status", nargs="+", default=["open", "upcoming"],
        choices=["open", "upcoming", "ended"], help="which Devpost open states to pull",
    )
    parser.add_argument("--mlh-season", nargs="+", type=int, help="MLH seasons (default: current)")
    parser.add_argument("--force", action="store_true", help="ignore the freshness cache")
    parser.add_argument("--no-rules", action="store_true",
                        help="jangan otomatis baca halaman aturan Devpost setelah scraping")
    parser.add_argument("--rules-budget", type=int, default=40,
                        help="maksimal halaman aturan yang dibaca per run")
    parser.add_argument(
        "--ttl", type=int, default=DEFAULT_TTL_MINUTES,
        help="minutes a source stays fresh before it is re-fetched (0 = always fetch)",
    )
    args = parser.parse_args(argv)

    options = ScrapeOptions(
        sources=list(SOURCES) if args.source == "all" else [args.source],
        force=args.force,
        ttl_minutes=args.ttl,
        max_pages=args.pages,
        devpost_statuses=args.devpost_status,
        mlh_seasons=args.mlh_season,
        auto_rules=not args.no_rules,
        rules_budget=args.rules_budget,
    )

    conn = connect()
    failed = False
    for result in scrape(options, conn):
        name = result["source"]
        if result["error"]:
            failed = True
            print(f"  {name:<8} FAILED: {result['error']}", file=sys.stderr)
        elif result.get("skipped"):
            print(f"  {name:<8} cached  (last sync {result['last_success']}) — pakai --force untuk paksa")
        else:
            print(f"  {name:<8} found={result['found']:<4} new={result['new']:<4} updated={result['updated']}")
            rules = result.get("rules")
            if rules and rules["total"]:
                print(f"  {'':<8} aturan: {rules['checked']} terbaca,"
                      f" {rules['restricted']} punya larangan negara, {rules['failed']} gagal")
            if result.get("rules_pending"):
                print(f"  {'':<8} sisa {result['rules_pending']} aturan belum dibaca"
                      f" — jalankan `python -m scraper.rules`")

    total = conn.execute("SELECT COUNT(*) FROM hackathons").fetchone()[0]
    live = conn.execute("SELECT COUNT(*) FROM hackathons WHERE status IN ('open','upcoming')").fetchone()[0]
    print(f"  db       total={total} live={live}")
    conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
