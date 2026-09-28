"""Daily digest: refresh everything, then pick the week's top 5.

Ranking reuses the same machinery as the dashboard — the profile built from your
board, the eligibility verdict, and your like/dismiss marks — so the digest can
never suggest something the app itself would refuse to recommend.

Each run writes a dated Markdown and HTML file under ``data/digests/`` and
compares against the previous digest so repeats are marked as carried over
rather than presented as fresh news every morning.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .db import connect, feedback_map, get_setting
from .eligibility import UserProfile, evaluate
from .models import iso, parse_iso, utcnow
from .recommend import build_profile, rank
from .rules import enrich as enrich_rules
from .run import ScrapeOptions, scrape

DIGEST_DIR = Path(__file__).resolve().parent.parent / "data" / "digests"

if os.environ.get("VERCEL"):
    # Same read-only constraint as the DB: serve and write digests from /tmp.
    _BUNDLED_DIGESTS, DIGEST_DIR = DIGEST_DIR, Path("/tmp/digests")
    if not DIGEST_DIR.exists() and _BUNDLED_DIGESTS.exists():
        shutil.copytree(_BUNDLED_DIGESTS, DIGEST_DIR)
TOP_N = 5


def local_date() -> str:
    """A daily digest is a human-calendar thing, so it is dated by the machine's
    local day. Timestamps inside it stay UTC — only the label is local."""
    return datetime.now().astimezone().date().isoformat()


def collect(conn, top_n: int = TOP_N) -> dict:
    """Score every enterable candidate and keep the best few."""
    now = utcnow()
    user = UserProfile.from_dict(get_setting(conn, "profile"))
    taste = build_profile(conn.execute(
        "SELECT h.* FROM tracked t JOIN hackathons h ON h.id = t.hackathon_id"
    ).fetchall())
    marks = feedback_map(conn)

    rows = conn.execute(
        "SELECT h.* FROM hackathons h LEFT JOIN tracked t ON t.hackathon_id = h.id"
        " WHERE h.status IN ('open','upcoming') AND t.hackathon_id IS NULL"
    ).fetchall()

    verdicts = {r["id"]: evaluate(r, user) for r in rows}
    candidates = [
        r for r in rows
        if marks.get(r["id"]) != "dismiss"
        and (marks.get(r["id"]) == "like" or verdicts[r["id"]]["verdict"] != "blocked")
    ]
    liked = {r["id"] for r in candidates if marks.get(r["id"]) == "like"}

    ranked = rank(candidates, taste, limit=max(top_n, len(candidates)), now=now)
    items = []
    for result, row in ranked:
        end = parse_iso(row["end_at"])
        items.append({
            "id": row["id"], "title": row["title"], "url": row["url"],
            "source": row["source"], "score": result["score"],
            "reasons": result["reasons"], "prize_amount": row["prize_amount"],
            "prize_currency": row["prize_currency"], "participants": row["participants"],
            "mode": row["mode"], "end_at": row["end_at"],
            "days_left": round((end - now).total_seconds() / 86400, 1) if end else None,
            "closes_this_week": bool(end and end <= now + timedelta(days=7)),
            "eligibility": verdicts[row["id"]],
            "pinned": row["id"] in liked,
        })
    items.sort(key=lambda i: (0 if i["pinned"] else 1, -i["score"]))
    top = items[:top_n]

    week_ago = iso(now - timedelta(days=7))
    context = {
        "new_this_week": conn.execute(
            "SELECT COUNT(*) FROM hackathons WHERE first_seen_at >= ?"
            " AND status IN ('open','upcoming')", (week_ago,),
        ).fetchone()[0],
        "closing_soon": conn.execute(
            "SELECT COUNT(*) FROM hackathons WHERE status = 'open' AND end_at IS NOT NULL"
            " AND end_at BETWEEN ? AND ?", (iso(now), iso(now + timedelta(days=7))),
        ).fetchone()[0],
        "your_deadlines": conn.execute(
            """SELECT COUNT(*) FROM tracked t JOIN hackathons h ON h.id = t.hackathon_id
               WHERE t.status NOT IN ('submitted','won','lost')
                 AND COALESCE(t.my_deadline, h.end_at) BETWEEN ? AND ?""",
            (iso(now), iso(now + timedelta(days=7))),
        ).fetchone()[0],
        "candidates": len(candidates),
        "excluded_ineligible": sum(
            1 for r in rows
            if verdicts[r["id"]]["verdict"] == "blocked" and marks.get(r["id"]) != "like"
        ),
    }

    return {
        "generated_at": iso(now),
        "date": local_date(),
        "profile": user.to_dict(),
        "cold_start": taste.is_cold,
        "top": top,
        "context": context,
    }


def previous_ids(before: str) -> set[str]:
    """Ids from the most recent earlier digest, to mark carry-overs."""
    if not DIGEST_DIR.exists():
        return set()
    earlier = sorted(p for p in DIGEST_DIR.glob("*.json") if p.stem < before)
    if not earlier:
        return set()
    try:
        data = json.loads(earlier[-1].read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    return {i["id"] for i in data.get("top", [])}


def money(amount, currency) -> str:
    if not amount:
        return "—"
    return ("€" if currency == "EUR" else "$") + f"{amount:,}".replace(",", ".")


def render_markdown(data: dict) -> str:
    ctx = data["context"]
    lines = [
        f"# Top {len(data['top'])} hackathon — {data['date']}",
        "",
        f"{ctx['candidates']} kandidat lolos syaratmu · {ctx['excluded_ineligible']} tersaring "
        f"karena tidak memenuhi syarat · {ctx['new_this_week']} baru masuk 7 hari terakhir",
    ]
    if ctx["your_deadlines"]:
        lines.append(f"\n> ⚠ {ctx['your_deadlines']} lomba di papanmu punya deadline dalam 7 hari.")
    if data["cold_start"]:
        lines.append("\n> Papanmu masih kosong, jadi peringkat memakai sinyal umum "
                     "(waktu persiapan, hadiah, jumlah pendaftar), belum seleramu.")
    lines.append("")

    for n, item in enumerate(data["top"], 1):
        flags = []
        if item["pinned"]:
            flags.append("♥ disematkan")
        if item.get("is_new"):
            flags.append("baru")
        elif item.get("is_new") is False:
            flags.append("lanjutan kemarin")
        if item["closes_this_week"]:
            flags.append("tutup minggu ini")
        head = f"## {n}. {item['title']} — {item['score']}/100"
        lines += [head, "", f"<{item['url']}>", ""]
        lines.append(
            f"- **Sumber**: {item['source']} · **Format**: {item['mode'] or '—'} · "
            f"**Hadiah**: {money(item['prize_amount'], item['prize_currency'])} · "
            f"**Pendaftar**: {item['participants'] or '—'}"
        )
        if item["days_left"] is not None:
            lines.append(f"- **Sisa waktu**: {item['days_left']} hari")
        if flags:
            lines.append(f"- **Catatan**: {', '.join(flags)}")
        lines.append(f"- **Kenapa**: {'; '.join(item['reasons'])}")
        e = item["eligibility"]
        if e["warnings"]:
            lines.append(f"- **Perlu dicek**: {'; '.join(e['warnings'])}")
        lines.append("")

    if not data["top"]:
        lines.append("_Tidak ada kandidat yang lolos syaratmu hari ini._")
    lines += ["---", "",
              f"Profil: {data['profile']['country']} · {data['profile']['student_level']} · "
              f"{data['profile']['travel']}. Skor bukan prediksi menang — hanya kecocokan. "
              "Selalu baca aturan resmi sebelum mendaftar."]
    return "\n".join(lines)


def render_html(data: dict) -> str:
    def esc(text) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    ctx = data["context"]
    cards = []
    for n, item in enumerate(data["top"], 1):
        chips = "".join(
            f'<span class="chip">{esc(c)}</span>' for c in [
                item["source"], item["mode"] or "",
                money(item["prize_amount"], item["prize_currency"]),
                f"{item['days_left']} hari lagi" if item["days_left"] is not None else "",
                "♥ disematkan" if item["pinned"] else "",
                "baru" if item.get("is_new") else "",
            ] if c
        )
        warn = item["eligibility"]["warnings"]
        cards.append(f"""
        <article>
          <h2><span class="rank">{n}</span> <a href="{esc(item['url'])}">{esc(item['title'])}</a>
              <span class="score">{item['score']}</span></h2>
          <div class="chips">{chips}</div>
          <ul>{''.join(f'<li>{esc(r)}</li>' for r in item['reasons'])}</ul>
          {f'<p class="warn">⚠ {esc("; ".join(warn))}</p>' if warn else ''}
        </article>""")

    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Top {len(data['top'])} hackathon — {data['date']}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
         max-width: 760px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size: 21px; margin: 0 0 4px; }}
  .sub {{ opacity: .65; font-size: 13px; margin-bottom: 22px; }}
  article {{ border: 1px solid rgba(128,128,128,.3); border-radius: 10px;
             padding: 14px 16px; margin-bottom: 12px; }}
  h2 {{ font-size: 15.5px; margin: 0 0 8px; display: flex; gap: 8px; align-items: baseline; }}
  h2 a {{ color: inherit; }}
  .rank {{ opacity: .45; }}
  .score {{ margin-left: auto; font-size: 13px; opacity: .7; }}
  .chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }}
  .chip {{ border: 1px solid rgba(128,128,128,.35); border-radius: 999px;
           padding: 1px 9px; font-size: 11.5px; opacity: .8; }}
  ul {{ margin: 0; padding-left: 18px; font-size: 13.5px; opacity: .85; }}
  .warn {{ font-size: 12.5px; color: #b7791f; margin: 8px 0 0; }}
  footer {{ margin-top: 26px; font-size: 12px; opacity: .6; }}
</style></head><body>
<h1>Top {len(data['top'])} hackathon — {data['date']}</h1>
<div class="sub">{ctx['candidates']} kandidat lolos syaratmu · {ctx['excluded_ineligible']} tersaring ·
{ctx['new_this_week']} baru masuk 7 hari terakhir
{f" · ⚠ {ctx['your_deadlines']} deadline di papanmu minggu ini" if ctx['your_deadlines'] else ""}</div>
{''.join(cards) or '<p>Tidak ada kandidat yang lolos syaratmu hari ini.</p>'}
<footer>Profil: {esc(data['profile']['country'])} · {esc(data['profile']['student_level'])} ·
{esc(data['profile']['travel'])}. Skor adalah kecocokan, bukan prediksi menang.
Selalu baca aturan resmi sebelum mendaftar.</footer>
</body></html>"""


def build(conn, top_n: int = TOP_N, write: bool = True) -> dict:
    data = collect(conn, top_n)
    seen = previous_ids(data["date"])
    for item in data["top"]:
        item["is_new"] = item["id"] not in seen if seen else None

    if write:
        DIGEST_DIR.mkdir(parents=True, exist_ok=True)
        stem = DIGEST_DIR / data["date"]
        stem.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
        stem.with_suffix(".md").write_text(render_markdown(data))
        stem.with_suffix(".html").write_text(render_html(data))
        (DIGEST_DIR / "latest.html").write_text(render_html(data))
        data["files"] = [str(stem.with_suffix(ext)) for ext in (".json", ".md", ".html")]
    return data


def run_daily(top_n: int = TOP_N, skip_scrape: bool = False, rules_budget: int = 60) -> dict:
    """Full morning job: scrape → read rules → build the digest."""
    conn = connect()
    try:
        scraped = []
        if not skip_scrape:
            # ttl 0 so the daily run always refreshes, whatever ran before it.
            scraped = scrape(ScrapeOptions(ttl_minutes=0, rules_budget=rules_budget), conn)
            # Anything the per-source budget could not cover, catch here.
            enrich_rules(conn, limit=rules_budget, sources=("devpost", "lablab", "mlh"))
        data = build(conn, top_n)
        data["scrape"] = scraped
        return data
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Digest harian: scrape, baca aturan, top N.")
    parser.add_argument("--top", type=int, default=TOP_N)
    parser.add_argument("--skip-scrape", action="store_true", help="pakai data yang sudah ada")
    parser.add_argument("--rules-budget", type=int, default=60)
    parser.add_argument("--print", action="store_true", help="cetak digest ke stdout")
    args = parser.parse_args(argv)

    data = run_daily(args.top, args.skip_scrape, args.rules_budget)
    for r in data.get("scrape", []):
        if r.get("error"):
            print(f"  {r['source']:<8} GAGAL: {r['error']}")
        elif r.get("skipped"):
            print(f"  {r['source']:<8} cached")
        else:
            print(f"  {r['source']:<8} {r['found']} ditemukan, {r['new']} baru")
    print(f"  digest  {len(data['top'])} lomba · {data['context']['candidates']} kandidat"
          f" · tersimpan di data/digests/{data['date']}.md")
    if args.print:
        print()
        print(render_markdown(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
