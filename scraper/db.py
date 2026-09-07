"""SQLite persistence: scraped hackathons + the user's own tracking board."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .models import Hackathon, classify_audience, iso, parse_iso, resolve_country, utcnow

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hackathons.db"

TRACK_STATUSES = [
    "interested",
    "registered",
    "building",
    "submitted",
    "won",
    "lost",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS hackathons (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    native_id      TEXT NOT NULL,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL,
    status         TEXT,
    mode           TEXT,
    location       TEXT,
    organizer      TEXT,
    description    TEXT,
    thumbnail      TEXT,
    start_at       TEXT,
    end_at         TEXT,
    deadline_text  TEXT,
    prize_amount   INTEGER,
    prize_currency TEXT,
    participants   INTEGER,
    themes         TEXT DEFAULT '[]',
    audience       TEXT,
    country        TEXT,
    invite_only    INTEGER,
    signup_open    INTEGER,
    eligibility_note TEXT,
    excluded_countries TEXT,
    requires_student INTEGER,
    eligibility_snippet TEXT,
    rules_checked_at TEXT,
    rules_ok       INTEGER,
    openness_claim TEXT,
    scraped_at     TEXT,
    first_seen_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_hack_source ON hackathons(source);
CREATE INDEX IF NOT EXISTS idx_hack_status ON hackathons(status);
CREATE INDEX IF NOT EXISTS idx_hack_end    ON hackathons(end_at);

CREATE TABLE IF NOT EXISTS tracked (
    hackathon_id  TEXT PRIMARY KEY REFERENCES hackathons(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'interested',
    priority      INTEGER NOT NULL DEFAULT 2,
    team          TEXT,
    project_name  TEXT,
    submission_url TEXT,
    notes         TEXT,
    my_deadline   TEXT,
    progress      INTEGER NOT NULL DEFAULT 0,
    checklist     TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS track_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hackathon_id TEXT NOT NULL REFERENCES hackathons(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    message      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_hack ON track_events(hackathon_id);

-- Explicit thumbs up / down on a recommendation. Separate from `tracked`:
-- liking something is not the same as joining it.
CREATE TABLE IF NOT EXISTS feedback (
    hackathon_id TEXT PRIMARY KEY REFERENCES hackathons(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('like', 'dismiss')),
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_kind ON feedback(kind);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    found       INTEGER DEFAULT 0,
    inserted    INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    error       TEXT
);
"""


# Columns added after the first release; applied to databases created earlier.
MIGRATIONS = {
    "hackathons": {
        "audience": "TEXT",
        "country": "TEXT",
        "invite_only": "INTEGER",
        "signup_open": "INTEGER",
        "eligibility_note": "TEXT",
        "excluded_countries": "TEXT",
        "requires_student": "INTEGER",
        "eligibility_snippet": "TEXT",
        "rules_checked_at": "TEXT",
        "rules_ok": "INTEGER",
        "openness_claim": "TEXT",
    },
    "tracked": {
        "my_deadline": "TEXT",
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "checklist": "TEXT NOT NULL DEFAULT '[]'",
    },
}


def migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hack_aud ON hackathons(audience)")
    conn.commit()
    backfill_audience(conn)
    backfill_country(conn)


def backfill_audience(conn: sqlite3.Connection) -> int:
    """Classify rows stored before the audience column existed."""
    rows = conn.execute(
        "SELECT id, source, title, organizer, location, themes, description"
        " FROM hackathons WHERE audience IS NULL"
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE hackathons SET audience = ? WHERE id = ?",
            (
                classify_audience(
                    r["source"], r["title"], r["organizer"], r["location"],
                    " ".join(json.loads(r["themes"] or "[]")), r["description"],
                ),
                r["id"],
            ),
        )
    conn.commit()
    return len(rows)


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


UPSERT_FIELDS = [
    "source", "native_id", "title", "url", "status", "mode", "location",
    "organizer", "description", "thumbnail", "start_at", "end_at",
    "deadline_text", "prize_amount", "prize_currency", "participants",
    "themes", "audience", "country", "invite_only", "signup_open",
    "eligibility_note", "scraped_at",
]


def upsert_hackathons(conn: sqlite3.Connection, items: list[Hackathon]) -> tuple[int, int]:
    """Insert new rows, refresh existing ones. Returns (inserted, updated)."""
    now = iso(utcnow())
    existing = {r["id"] for r in conn.execute("SELECT id FROM hackathons")}
    inserted = updated = 0
    assignments = ", ".join(f"{f} = excluded.{f}" for f in UPSERT_FIELDS)
    sql = (
        f"INSERT INTO hackathons (id, {', '.join(UPSERT_FIELDS)}, first_seen_at) "
        f"VALUES (:id, {', '.join(':' + f for f in UPSERT_FIELDS)}, :first_seen_at) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}"
    )
    for h in items:
        row = h.to_row()
        row["first_seen_at"] = now
        conn.execute(sql, row)
        if h.id in existing:
            updated += 1
        else:
            inserted += 1
    conn.commit()
    return inserted, updated


def log_run(conn, source, started_at, found, inserted, updated, error=None) -> None:
    conn.execute(
        "INSERT INTO scrape_runs (source, started_at, finished_at, found, inserted, updated, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, started_at, iso(utcnow()), found, inserted, updated, error),
    )
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if isinstance(d.get("themes"), str):
        try:
            d["themes"] = json.loads(d["themes"])
        except json.JSONDecodeError:
            d["themes"] = []
    return d


def last_success(conn: sqlite3.Connection, source: str) -> str | None:
    """finished_at of the most recent error-free run for a source."""
    row = conn.execute(
        "SELECT finished_at FROM scrape_runs WHERE source = ? AND error IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (source,),
    ).fetchone()
    return row["finished_at"] if row else None


def is_fresh(conn: sqlite3.Connection, source: str, ttl_minutes: int) -> bool:
    """True when a source was scraped recently enough to reuse what is stored."""
    if ttl_minutes <= 0:
        return False
    stamp = parse_iso(last_success(conn, source))
    if not stamp:
        return False
    return (utcnow() - stamp) < timedelta(minutes=ttl_minutes)


def add_event(conn: sqlite3.Connection, hackathon_id: str, kind: str, message: str) -> None:
    conn.execute(
        "INSERT INTO track_events (hackathon_id, kind, message, created_at) VALUES (?, ?, ?, ?)",
        (hackathon_id, kind, message, iso(utcnow())),
    )
    conn.commit()


def events_for(conn: sqlite3.Connection, hackathon_id: str, limit: int = 50) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id, kind, message, created_at FROM track_events WHERE hackathon_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (hackathon_id, limit),
        )
    ]


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def backfill_country(conn: sqlite3.Connection) -> int:
    """Resolve countries for onsite rows stored before the column existed."""
    rows = conn.execute(
        "SELECT id, location FROM hackathons"
        " WHERE country IS NULL AND mode IN ('onsite','hybrid') AND location IS NOT NULL"
    ).fetchall()
    hits = 0
    for r in rows:
        code = resolve_country(r["location"])
        if code:
            conn.execute("UPDATE hackathons SET country = ? WHERE id = ?", (code, r["id"]))
            hits += 1
    conn.commit()
    return hits


FEEDBACK_KINDS = ("like", "dismiss")


def set_feedback(conn: sqlite3.Connection, hackathon_id: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO feedback (hackathon_id, kind, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(hackathon_id) DO UPDATE SET kind = excluded.kind,"
        " created_at = excluded.created_at",
        (hackathon_id, kind, iso(utcnow())),
    )
    conn.commit()


def clear_feedback(conn: sqlite3.Connection, hackathon_id: str) -> int:
    cur = conn.execute("DELETE FROM feedback WHERE hackathon_id = ?", (hackathon_id,))
    conn.commit()
    return cur.rowcount


def feedback_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["hackathon_id"]: r["kind"] for r in conn.execute("SELECT hackathon_id, kind FROM feedback")}
