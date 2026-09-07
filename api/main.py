"""FastAPI backend: read the scraped feed, manage the personal tracking board."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scraper.db import (
    FEEDBACK_KINDS,
    TRACK_STATUSES,
    add_event,
    clear_feedback,
    connect,
    events_for,
    feedback_map,
    get_setting,
    last_success,
    row_to_dict,
    set_feedback,
    set_setting,
)
from scraper.eligibility import STUDENT_LEVELS, TRAVEL_MODES, UserProfile, evaluate
from scraper.models import iso, parse_iso, utcnow
from scraper.recommend import build_profile, rank
from scraper.digest import DIGEST_DIR, build as build_digest
from scraper.rules import enrich as enrich_rules
from scraper.run import DEFAULT_TTL_MINUTES, ScrapeOptions, scrape
from scraper.sources import SOURCES

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Hackathon Screening", version="0.2.0")


@contextmanager
def db():
    """sqlite3's own context manager only wraps transactions, not the handle."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- models


class ChecklistItem(BaseModel):
    text: str
    done: bool = False


class TrackIn(BaseModel):
    status: str = Field(default="interested")
    priority: int = Field(default=2, ge=1, le=3)
    team: str | None = None
    project_name: str | None = None
    submission_url: str | None = None
    notes: str | None = None
    my_deadline: str | None = None
    progress: int = Field(default=0, ge=0, le=100)
    checklist: list[ChecklistItem] = Field(default_factory=list)


class EventIn(BaseModel):
    message: str
    kind: str = "note"


class ScrapeIn(BaseModel):
    sources: list[str] | None = None
    force: bool = False
    ttl_minutes: int = DEFAULT_TTL_MINUTES
    max_pages: int = Field(default=8, ge=1, le=30)
    devpost_statuses: list[str] = Field(default_factory=lambda: ["open", "upcoming"])
    mlh_seasons: list[int] | None = None
    auto_rules: bool = True
    rules_budget: int = Field(default=40, ge=0, le=300)


# --------------------------------------------------------------------------- queries

TRACK_COLUMNS = [
    "status", "priority", "team", "project_name", "submission_url",
    "notes", "my_deadline", "progress", "checklist", "updated_at",
]

BASE_SELECT = f"""
SELECT h.*, {', '.join(f't.{c} AS track_{c}' for c in TRACK_COLUMNS)}
FROM hackathons h
LEFT JOIN tracked t ON t.hackathon_id = h.id
"""


def load_profile(conn) -> UserProfile:
    return UserProfile.from_dict(get_setting(conn, "profile"))


def shape(row, profile: UserProfile | None = None, marks: dict | None = None) -> dict:
    d = row_to_dict(row)
    if profile is not None:
        d["eligibility"] = evaluate(row, profile)
    if marks is not None:
        d["feedback"] = marks.get(d["id"])
    raw = d.get("track_checklist")
    if isinstance(raw, str):
        try:
            d["track_checklist"] = json.loads(raw)
        except json.JSONDecodeError:
            d["track_checklist"] = []
    elif raw is None:
        d["track_checklist"] = []
    return d


@app.get("/api/hackathons")
def list_hackathons(
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    tracked: bool | None = None,
    mode: str | None = None,
    theme: str | None = None,
    audience: str | None = None,
    min_prize: int | None = None,
    has_prize: bool | None = None,
    min_participants: int | None = None,
    max_days: int | None = None,
    ends_within_days: int | None = None,
    new_within_days: int | None = None,
    eligibility: str | None = Query(None, pattern="^(eligible|check|blocked|joinable)$"),
    sort: str = Query("deadline", pattern="^(deadline|prize|participants|title|added)$"),
    limit: int = Query(500, le=2000),
):
    clauses: list[str] = []
    params: list = []
    if source:
        clauses.append(f"h.source IN ({','.join('?' * len(source.split(',')))})")
        params += source.split(",")
    if status:
        clauses.append(f"h.status IN ({','.join('?' * len(status.split(',')))})")
        params += status.split(",")
    if mode:
        clauses.append("h.mode = ?")
        params.append(mode)
    if audience:
        clauses.append("h.audience = ?")
        params.append(audience)
    if theme:
        clauses.append("LOWER(h.themes) LIKE ?")
        params.append(f"%{theme.lower()}%")
    if q:
        clauses.append(
            "(LOWER(h.title) LIKE ? OR LOWER(COALESCE(h.themes,'')) LIKE ?"
            " OR LOWER(COALESCE(h.organizer,'')) LIKE ?"
            " OR LOWER(COALESCE(h.location,'')) LIKE ?)"
        )
        needle = f"%{q.lower()}%"
        params += [needle] * 4
    if tracked is True:
        clauses.append("t.hackathon_id IS NOT NULL")
    elif tracked is False:
        clauses.append("t.hackathon_id IS NULL")
    if min_prize is not None:
        clauses.append("COALESCE(h.prize_amount, 0) >= ?")
        params.append(min_prize)
    if has_prize:
        clauses.append("COALESCE(h.prize_amount, 0) > 0")
    if min_participants is not None:
        clauses.append("COALESCE(h.participants, 0) >= ?")
        params.append(min_participants)
    if max_days is not None:
        # duration in whole days; rows without a start date are kept out
        clauses.append("h.start_at IS NOT NULL AND h.end_at IS NOT NULL"
                       " AND julianday(h.end_at) - julianday(h.start_at) <= ?")
        params.append(max_days)
    if new_within_days is not None:
        clauses.append("h.first_seen_at >= datetime(?, ?)")
        params += [iso(utcnow()), f"-{new_within_days} days"]
    if ends_within_days is not None:
        clauses.append("h.end_at IS NOT NULL AND h.end_at >= ? AND h.end_at <= datetime(?, ?)")
        now = iso(utcnow())
        params += [now, now, f"+{ends_within_days} days"]

    order = {
        "deadline": "h.end_at IS NULL, h.end_at",
        "prize": "COALESCE(h.prize_amount, -1) DESC",
        "participants": "COALESCE(h.participants, -1) DESC",
        "title": "LOWER(h.title)",
        "added": "h.first_seen_at DESC",
    }[sort]

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""{BASE_SELECT}{where}
    ORDER BY CASE h.status WHEN 'open' THEN 0 WHEN 'upcoming' THEN 1 ELSE 2 END, {order}"""

    with db() as conn:
        profile = load_profile(conn)
        marks = feedback_map(conn)
        # Eligibility is computed in Python, so it has to be applied before the
        # limit — otherwise a narrow verdict filter would silently return an
        # empty page whenever the first `limit` rows all fail it.
        rows = conn.execute(sql, params).fetchall()

    items = [shape(r, profile, marks) for r in rows]
    if eligibility:
        wanted = ("eligible", "check") if eligibility == "joinable" else (eligibility,)
        items = [i for i in items if i["eligibility"]["verdict"] in wanted]
    total = len(items)
    return {"count": min(len(items), limit), "total": total, "items": items[:limit]}


@app.get("/api/hackathons/{hackathon_id:path}")
def get_hackathon(hackathon_id: str):
    with db() as conn:
        row = conn.execute(BASE_SELECT + " WHERE h.id = ?", (hackathon_id,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown hackathon id")
        data = shape(row, load_profile(conn), feedback_map(conn))
        data["events"] = events_for(conn, hackathon_id)
    return data


@app.get("/api/facets")
def facets():
    """Distinct values the filter UI offers, counted."""
    with db() as conn:
        themes: dict[str, int] = {}
        for row in conn.execute("SELECT themes FROM hackathons"):
            for name in json.loads(row["themes"] or "[]"):
                themes[name] = themes.get(name, 0) + 1
        modes = {
            r["mode"]: r["n"]
            for r in conn.execute(
                "SELECT mode, COUNT(*) n FROM hackathons WHERE mode IS NOT NULL GROUP BY mode"
            )
        }
        audiences = {
            r["audience"]: r["n"]
            for r in conn.execute(
                "SELECT audience, COUNT(*) n FROM hackathons WHERE audience IS NOT NULL"
                " GROUP BY audience"
            )
        }
    top = sorted(themes.items(), key=lambda kv: -kv[1])[:40]
    return {
        "themes": [{"name": k, "count": v} for k, v in top],
        "modes": [{"name": k, "count": v} for k, v in sorted(modes.items(), key=lambda kv: -kv[1])],
        "audiences": [{"name": k, "count": v} for k, v in sorted(audiences.items(), key=lambda kv: -kv[1])],
        "sources": list(SOURCES),
        "track_statuses": TRACK_STATUSES,
    }


# --------------------------------------------------------------------------- tracking


@app.get("/api/tracked")
def list_tracked():
    sql = BASE_SELECT + """
    WHERE t.hackathon_id IS NOT NULL
    ORDER BY t.priority, h.end_at IS NULL, h.end_at"""
    with db() as conn:
        profile = load_profile(conn)
        rows = conn.execute(sql).fetchall()
    return {"count": len(rows), "items": [shape(r, profile) for r in rows]}


@app.put("/api/tracked/{hackathon_id:path}")
def upsert_tracked(hackathon_id: str, body: TrackIn):
    if body.status not in TRACK_STATUSES:
        raise HTTPException(400, f"status must be one of {TRACK_STATUSES}")
    now = iso(utcnow())
    with db() as conn:
        row = conn.execute(
            "SELECT h.title, t.status AS old_status, t.progress AS old_progress"
            " FROM hackathons h LEFT JOIN tracked t ON t.hackathon_id = h.id WHERE h.id = ?",
            (hackathon_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "unknown hackathon id")

        conn.execute(
            """
            INSERT INTO tracked (hackathon_id, status, priority, team, project_name,
                                 submission_url, notes, my_deadline, progress, checklist,
                                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hackathon_id) DO UPDATE SET
                status = excluded.status,
                priority = excluded.priority,
                team = excluded.team,
                project_name = excluded.project_name,
                submission_url = excluded.submission_url,
                notes = excluded.notes,
                my_deadline = excluded.my_deadline,
                progress = excluded.progress,
                checklist = excluded.checklist,
                updated_at = excluded.updated_at
            """,
            (
                hackathon_id, body.status, body.priority, body.team, body.project_name,
                body.submission_url, body.notes, body.my_deadline, body.progress,
                json.dumps([i.model_dump() for i in body.checklist], ensure_ascii=False),
                now, now,
            ),
        )
        conn.commit()

        # Timeline: only log the transitions worth remembering.
        if row["old_status"] is None:
            add_event(conn, hackathon_id, "tracked", f"Mulai dilacak — status {body.status}")
        elif row["old_status"] != body.status:
            add_event(conn, hackathon_id, "status", f"{row['old_status']} → {body.status}")
        elif (row["old_progress"] or 0) != body.progress:
            add_event(conn, hackathon_id, "progress", f"Progress {row['old_progress'] or 0}% → {body.progress}%")

    return {"ok": True, "hackathon_id": hackathon_id, "status": body.status}


@app.delete("/api/tracked/{hackathon_id:path}")
def untrack(hackathon_id: str):
    with db() as conn:
        cur = conn.execute("DELETE FROM tracked WHERE hackathon_id = ?", (hackathon_id,))
        conn.execute("DELETE FROM track_events WHERE hackathon_id = ?", (hackathon_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "not tracked")
    return {"ok": True}


@app.post("/api/tracked/{hackathon_id:path}/events")
def add_note(hackathon_id: str, body: EventIn):
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM tracked WHERE hackathon_id = ?", (hackathon_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "not tracked")
        add_event(conn, hackathon_id, body.kind, body.message)
        return {"ok": True, "events": events_for(conn, hackathon_id)}


@app.get("/api/agenda")
def agenda(days: int = 30):
    """Tracked hackathons with a deadline coming up — official or personal."""
    now = iso(utcnow())
    sql = BASE_SELECT + """
    WHERE t.hackathon_id IS NOT NULL
      AND t.status NOT IN ('submitted', 'won', 'lost')
      AND COALESCE(t.my_deadline, h.end_at) IS NOT NULL
      AND COALESCE(t.my_deadline, h.end_at) >= ?
      AND COALESCE(t.my_deadline, h.end_at) <= datetime(?, ?)
    ORDER BY COALESCE(t.my_deadline, h.end_at)"""
    with db() as conn:
        profile = load_profile(conn)
        rows = conn.execute(sql, (now, now, f"+{days} days")).fetchall()
    return {"count": len(rows), "items": [shape(r, profile) for r in rows]}


class ProfileIn(BaseModel):
    country: str = Field(default="ID", min_length=2, max_length=2)
    is_student: bool = False
    student_level: str = "none"
    travel: str = "online_only"
    allow_invite_only: bool = False


@app.get("/api/profile")
def get_profile():
    with db() as conn:
        profile = load_profile(conn)
        rows = conn.execute(
            "SELECT * FROM hackathons WHERE status IN ('open','upcoming')"
        ).fetchall()
    counts = {"eligible": 0, "check": 0, "blocked": 0}
    for row in rows:
        counts[evaluate(row, profile)["verdict"]] += 1
    return {
        "profile": profile.to_dict(),
        "options": {"travel": list(TRAVEL_MODES), "student_level": list(STUDENT_LEVELS)},
        "impact": counts,
    }


@app.put("/api/profile")
def put_profile(body: ProfileIn):
    if body.travel not in TRAVEL_MODES:
        raise HTTPException(400, f"travel must be one of {list(TRAVEL_MODES)}")
    if body.student_level not in STUDENT_LEVELS:
        raise HTTPException(400, f"student_level must be one of {list(STUDENT_LEVELS)}")
    profile = UserProfile.from_dict(body.model_dump())
    with db() as conn:
        set_setting(conn, "profile", profile.to_dict())
    return {"ok": True, "profile": profile.to_dict()}


class FeedbackIn(BaseModel):
    kind: str  # like | dismiss


@app.get("/api/feedback")
def list_feedback():
    sql = BASE_SELECT + """
    JOIN feedback f ON f.hackathon_id = h.id
    ORDER BY f.kind, f.created_at DESC"""
    with db() as conn:
        profile = load_profile(conn)
        rows = conn.execute(sql).fetchall()
        kinds = feedback_map(conn)
    items = []
    for row in rows:
        item = shape(row, profile)
        item["feedback"] = kinds.get(item["id"])
        items.append(item)
    return {
        "count": len(items),
        "likes": sum(1 for i in items if i["feedback"] == "like"),
        "dismissed": sum(1 for i in items if i["feedback"] == "dismiss"),
        "items": items,
    }


@app.put("/api/feedback/{hackathon_id:path}")
def put_feedback(hackathon_id: str, body: FeedbackIn):
    if body.kind not in FEEDBACK_KINDS:
        raise HTTPException(400, f"kind must be one of {list(FEEDBACK_KINDS)}")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM hackathons WHERE id = ?", (hackathon_id,)).fetchone():
            raise HTTPException(404, "unknown hackathon id")
        set_feedback(conn, hackathon_id, body.kind)
    return {"ok": True, "hackathon_id": hackathon_id, "kind": body.kind}


@app.delete("/api/feedback/{hackathon_id:path}")
def delete_feedback(hackathon_id: str):
    with db() as conn:
        removed = clear_feedback(conn, hackathon_id)
    if not removed:
        raise HTTPException(404, "no feedback stored for this hackathon")
    return {"ok": True}


@app.get("/api/recommendations")
def recommendations(
    limit: int = Query(20, le=100),
    audience: str | None = None,
    mode: str | None = None,
    source: str | None = None,
    max_days: int | None = None,
    include_tracked: bool = False,
    include_ineligible: bool = False,
    include_dismissed: bool = False,
):
    """Rank hackathons you are not on yet against what your board says you like."""
    clauses = ["h.status IN ('open','upcoming')"]
    params: list = []
    if not include_tracked:
        clauses.append("t.hackathon_id IS NULL")
    if audience:
        clauses.append("h.audience = ?")
        params.append(audience)
    if mode:
        clauses.append("h.mode = ?")
        params.append(mode)
    if source:
        clauses.append(f"h.source IN ({','.join('?' * len(source.split(',')))})")
        params += source.split(",")
    if max_days is not None:
        clauses.append("h.end_at IS NOT NULL AND h.end_at <= datetime(?, ?)")
        params += [iso(utcnow()), f"+{max_days} days"]

    with db() as conn:
        user = load_profile(conn)
        profile = build_profile(conn.execute(
            "SELECT h.* FROM tracked t JOIN hackathons h ON h.id = t.hackathon_id"
        ).fetchall())
        raw_candidates = conn.execute(
            "SELECT h.* FROM hackathons h LEFT JOIN tracked t ON t.hackathon_id = h.id"
            " WHERE " + " AND ".join(clauses),
            params,
        ).fetchall()
        marks = feedback_map(conn)

        verdicts = {row["id"]: evaluate(row, user) for row in raw_candidates}
        liked = {row["id"] for row in raw_candidates if marks.get(row["id"]) == "like"}

        def keep(row) -> bool:
            mark = marks.get(row["id"])
            if mark == "dismiss" and not include_dismissed:
                return False
            # A like is an explicit choice, so it survives the eligibility cut —
            # the verdict chip still travels with it so nothing is hidden.
            if mark == "like":
                return True
            return include_ineligible or verdicts[row["id"]]["verdict"] != "blocked"

        excluded = sum(
            1 for row in raw_candidates
            if verdicts[row["id"]]["verdict"] == "blocked" and marks.get(row["id"]) != "like"
        )
        dismissed = sum(1 for row in raw_candidates if marks.get(row["id"]) == "dismiss")
        candidates = [row for row in raw_candidates if keep(row)]
        # Liked entries are pinned, so rank deep enough that they are all scored.
        ranked = rank(candidates, profile, limit=max(limit, len(candidates)))

    items = []
    for result, row in ranked:
        item = row_to_dict(row)
        item["score"] = result["score"]
        item["reasons"] = result["reasons"]
        item["matched_themes"] = result["matched_themes"]
        item["eligibility"] = verdicts[row["id"]]
        item["feedback"] = marks.get(row["id"])
        items.append(item)

    # Pinned first, then by score — rank() already ordered within each group.
    items.sort(key=lambda i: (0 if i["id"] in liked else 1, -i["score"]))
    items = items[:limit]

    return {
        "count": len(items),
        "candidates": len(candidates),
        "excluded_ineligible": excluded,
        "excluded_dismissed": dismissed,
        "pinned": len(liked),
        "user_profile": user.to_dict(),
        "profile": {
            "cold_start": profile.is_cold,
            "sample_size": profile.sample_size,
            "top_themes": profile.top_themes,
            "audience": profile.audiences.most_common(1)[0][0] if profile.audiences else None,
            "mode": profile.modes.most_common(1)[0][0] if profile.modes else None,
            "median_prize": profile.median_prize,
        },
        "items": items,
    }


@app.get("/api/notifications")
def notifications(horizon_days: int = 14):
    """Deadline alerts for tracked hackathons you have not submitted yet.

    Severity comes from how much time is left against the effective deadline —
    your personal target when set, otherwise the official one.
    """
    now = utcnow()
    sql = BASE_SELECT + """
    WHERE t.hackathon_id IS NOT NULL
      AND t.status NOT IN ('submitted', 'won', 'lost')
      AND COALESCE(t.my_deadline, h.end_at) IS NOT NULL
      AND COALESCE(t.my_deadline, h.end_at) <= datetime(?, ?)
    ORDER BY COALESCE(t.my_deadline, h.end_at)"""
    with db() as conn:
        rows = conn.execute(sql, (iso(now), f"+{horizon_days} days")).fetchall()

    items = []
    for row in rows:
        h = shape(row)
        deadline = parse_iso(h.get("track_my_deadline") or h.get("end_at"))
        if not deadline:
            continue
        hours = (deadline - now).total_seconds() / 3600
        personal = bool(h.get("track_my_deadline"))
        label = "target pribadi" if personal else "deadline"

        if hours < 0:
            level, message = "overdue", f"{label.capitalize()} sudah lewat {int(-hours)} jam lalu — belum ditandai submit"
        elif hours <= 24:
            level, message = "critical", f"{label.capitalize()} tinggal {max(int(hours), 0)} jam lagi"
        elif hours <= 72:
            level, message = "warning", f"{label.capitalize()} tinggal {int(hours / 24)} hari lagi"
        elif hours <= 24 * 7:
            level, message = "soon", f"{label.capitalize()} {int(hours / 24)} hari lagi"
        else:
            level, message = "info", f"{label.capitalize()} {int(hours / 24)} hari lagi"

        items.append({
            "id": h["id"],
            "title": h["title"],
            "source": h["source"],
            "level": level,
            "message": message,
            "deadline": iso(deadline),
            "hours_left": round(hours, 1),
            "personal": personal,
            "track_status": h.get("track_status"),
            "progress": h.get("track_progress") or 0,
        })

    counts: dict[str, int] = {}
    for item in items:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
    # Only the genuinely urgent ones drive the badge and the desktop popup.
    urgent = [i for i in items if i["level"] in ("overdue", "critical", "warning")]
    return {"count": len(items), "urgent": len(urgent), "by_level": counts, "items": items}


@app.get("/api/activity")
def activity(limit: int = 20):
    with db() as conn:
        rows = conn.execute(
            """SELECT e.*, h.title FROM track_events e
               JOIN hackathons h ON h.id = e.hackathon_id
               ORDER BY e.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


# --------------------------------------------------------------------------- ops


@app.get("/api/stats")
def stats():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM hackathons").fetchone()[0]
        by_source = {r["source"]: r["n"] for r in conn.execute(
            "SELECT source, COUNT(*) n FROM hackathons GROUP BY source")}
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM hackathons GROUP BY status")}
        by_track = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM tracked GROUP BY status")}
        active_prize = conn.execute(
            """SELECT COALESCE(SUM(h.prize_amount), 0) FROM tracked t
               JOIN hackathons h ON h.id = t.hackathon_id
               WHERE t.status NOT IN ('lost', 'won')"""
        ).fetchone()[0]
        now = iso(utcnow())
        urgent = conn.execute(
            """SELECT COUNT(*) FROM tracked t JOIN hackathons h ON h.id = t.hackathon_id
               WHERE COALESCE(t.my_deadline, h.end_at) IS NOT NULL
                 AND COALESCE(t.my_deadline, h.end_at) >= ?
                 AND COALESCE(t.my_deadline, h.end_at) <= datetime(?, '+7 days')
                 AND t.status NOT IN ('submitted','won','lost')""",
            (now, now),
        ).fetchone()[0]
        submitted = conn.execute(
            "SELECT COUNT(*) FROM tracked WHERE status IN ('submitted','won','lost')"
        ).fetchone()[0]
        won = by_track.get("won", 0)
    return {
        "total": total,
        "by_source": by_source,
        "by_status": by_status,
        "tracked_by_status": by_track,
        "tracked_total": sum(by_track.values()),
        "active_prize_pool": active_prize,
        "deadlines_this_week": urgent,
        "submitted_total": submitted,
        "win_rate": round(won / submitted * 100) if submitted else 0,
    }


@app.get("/api/sources")
def sources_status():
    """Per-source freshness so the UI can show what is cached vs stale."""
    out = []
    with db() as conn:
        for name in SOURCES:
            row = conn.execute(
                "SELECT COUNT(*) n, SUM(status IN ('open','upcoming')) live"
                " FROM hackathons WHERE source = ?", (name,),
            ).fetchone()
            run = conn.execute(
                "SELECT finished_at, found, inserted, updated, error FROM scrape_runs"
                " WHERE source = ? ORDER BY id DESC LIMIT 1", (name,),
            ).fetchone()
            out.append({
                "source": name,
                "stored": row["n"] or 0,
                "live": row["live"] or 0,
                "last_success": last_success(conn, name),
                "last_run": dict(run) if run else None,
            })
    return {"items": out, "ttl_minutes": DEFAULT_TTL_MINUTES}


@app.get("/api/runs")
def runs(limit: int = 20):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, source, started_at, finished_at, found, inserted, updated, error"
            " FROM scrape_runs ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/scrape")
def trigger_scrape(body: ScrapeIn | None = None):
    body = body or ScrapeIn()
    names = body.sources or list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise HTTPException(400, f"unknown source(s): {unknown}")
    options = ScrapeOptions(
        sources=names,
        force=body.force,
        ttl_minutes=body.ttl_minutes,
        max_pages=body.max_pages,
        devpost_statuses=body.devpost_statuses,
        mlh_seasons=body.mlh_seasons,
        auto_rules=body.auto_rules,
        rules_budget=body.rules_budget,
    )
    with db() as conn:
        return {"results": scrape(options, conn)}


@app.post("/api/rules/check")
def check_rules(
    limit: int = Query(60, le=300),
    force: bool = False,
    sources: str = Query("devpost,lablab,mlh"),
):
    """Fetch rules pages for events whose eligibility is still unknown."""
    names = tuple(s for s in sources.split(",") if s in SOURCES)
    if not names:
        raise HTTPException(400, "no valid source given")
    with db() as conn:
        return enrich_rules(conn, limit=limit, force=force, sources=names)


@app.get("/api/rules/status")
def rules_status():
    """Coverage per source — verified, unreadable, and never attempted."""
    with db() as conn:
        rows = conn.execute(
            """SELECT source,
                 COUNT(*) AS live,
                 SUM(rules_ok = 1) AS verified,
                 SUM(rules_checked_at IS NOT NULL AND rules_ok = 0) AS unreadable,
                 SUM(rules_checked_at IS NULL) AS never_tried,
                 SUM(openness_claim IS NOT NULL) AS claims_open,
                 SUM(excluded_countries IS NOT NULL AND excluded_countries NOT IN ('[]')) AS with_limits,
                 SUM(requires_student = 1) AS student_only
               FROM hackathons WHERE status IN ('open','upcoming') GROUP BY source"""
        ).fetchall()
        profile = load_profile(conn)
        blocked_for_me = conn.execute(
            "SELECT COUNT(*) FROM hackathons WHERE status IN ('open','upcoming')"
            " AND excluded_countries LIKE ?", (f'%"{profile.country}"%',),
        ).fetchone()[0]

    by_source = []
    totals = {"live": 0, "verified": 0, "unreadable": 0, "pending": 0}
    for r in rows:
        live, verified, unreadable = r["live"] or 0, r["verified"] or 0, r["unreadable"] or 0
        entry = {
            "source": r["source"], "live": live, "verified": verified,
            "unreadable": unreadable, "pending": r["never_tried"] or 0,
            "claims_open": r["claims_open"] or 0,
            "with_country_limits": r["with_limits"] or 0,
            "student_only": r["student_only"] or 0,
        }
        by_source.append(entry)
        for k in totals:
            totals[k] += entry[k]

    return {
        "by_source": by_source,
        "totals": totals,
        "blocked_for_your_country": blocked_for_me,
        "your_country": profile.country,
        # Kept for the older UI shape.
        "devpost_live": next((e["live"] for e in by_source if e["source"] == "devpost"), 0),
        "checked": next((e["verified"] for e in by_source if e["source"] == "devpost"), 0),
    }


@app.get("/api/digest")
def latest_digest(date: str | None = None):
    """Today's top-5 digest, or a past one by date (YYYY-MM-DD)."""
    if date:
        path = DIGEST_DIR / f"{date}.json"
        if not path.is_file():
            raise HTTPException(404, "no digest for that date")
        return json.loads(path.read_text())
    files = sorted(DIGEST_DIR.glob("20*.json")) if DIGEST_DIR.exists() else []
    if not files:
        raise HTTPException(404, "no digest generated yet")
    return json.loads(files[-1].read_text())


@app.get("/api/digests")
def list_digests():
    if not DIGEST_DIR.exists():
        return {"items": []}
    return {"items": sorted(p.stem for p in DIGEST_DIR.glob("20*.json"))[::-1]}


@app.post("/api/digest/build")
def make_digest(top: int = Query(5, ge=1, le=20)):
    """Rebuild the digest from stored data — no scraping, so it is instant."""
    with db() as conn:
        return build_digest(conn, top_n=top)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
