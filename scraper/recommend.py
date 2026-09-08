"""Recommendation scoring.

There is no rating or "similar events" data on any source, so ranking is a
transparent weighted heuristic over what we do store. Two modes:

* **Berprofil** — once you track anything, your board becomes the profile:
  which themes, audience, format, source and prize range you keep picking.
* **Cold start** — with an empty board, only the neutral signals count
  (waktu persiapan, hadiah, jumlah pendaftar).

Every score ships with the reasons that produced it, so a suggestion can always
be argued with instead of just trusted.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .models import parse_iso, utcnow

# Weights sum to 100 in profiled mode.
W_THEME = 32
W_AUDIENCE = 14
W_MODE = 8
W_SOURCE = 4
W_TIMING = 22
W_PRIZE = 12
W_TRACTION = 8

# With no profile, its weight is folded into the neutral signals.
COLD_TIMING = 45
COLD_PRIZE = 33
COLD_TRACTION = 22

# A build window shorter than this is hard to enter cold; longer than this and
# the deadline is too far away to act on now.
IDEAL_MIN_DAYS = 5
IDEAL_MAX_DAYS = 45

# Not every source publishes every signal. MLH's season feed carries no prize
# pool and no registration count at all, so scoring those as zero used to cost
# each MLH event 55 of the 100 cold-start points before anything was compared —
# no MLH event could clear 45/100, and none ever reached a daily top five.
# Missing data is now dropped from the sum instead of counted as a bad score.
#
# Renormalizing alone would over-reward a row that we simply know nothing about,
# so the result is scaled by how much of the total weight was actually observed:
# a fully-evidenced row keeps its score, a row carried by one signal is capped
# near MIN_CONFIDENCE of it.
MIN_CONFIDENCE = 0.70


def _blend(parts: list[tuple[float, float]]) -> float:
    """Weighted average over observed signals only, scaled by their coverage.

    `parts` is a list of (weight, value) for signals that exist for this row;
    weights of absent signals are simply never passed in.
    """
    total = sum(w for w, _ in parts)
    if not total:
        return 0.0
    average = sum(w * v for w, v in parts) / total
    coverage = total / 100.0
    return average * (MIN_CONFIDENCE + (1 - MIN_CONFIDENCE) * coverage) * 100


@dataclass
class Profile:
    """What the tracked board says about your taste."""

    themes: Counter = field(default_factory=Counter)
    audiences: Counter = field(default_factory=Counter)
    modes: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    prizes: list[int] = field(default_factory=list)
    sample_size: int = 0

    @property
    def is_cold(self) -> bool:
        return self.sample_size == 0

    @property
    def top_themes(self) -> list[str]:
        return [t for t, _ in self.themes.most_common(6)]

    @property
    def median_prize(self) -> int | None:
        if not self.prizes:
            return None
        ordered = sorted(self.prizes)
        return ordered[len(ordered) // 2]

    def share(self, counter: Counter, key: str | None) -> float:
        """How much of the board a given value accounts for, 0..1."""
        total = sum(counter.values())
        if not total or not key:
            return 0.0
        return counter.get(key, 0) / total


def build_profile(tracked_rows) -> Profile:
    """`tracked_rows` are joined hackathon+tracked rows already on the board."""
    p = Profile()
    for row in tracked_rows:
        # Lost entries still say something about taste, but count for less.
        p.sample_size += 1
        for theme in json.loads(row["themes"] or "[]"):
            p.themes[theme] += 1
        if row["audience"]:
            p.audiences[row["audience"]] += 1
        if row["mode"]:
            p.modes[row["mode"]] += 1
        p.sources[row["source"]] += 1
        if row["prize_amount"]:
            p.prizes.append(row["prize_amount"])
    return p


def _timing_score(end_at: datetime | None, now: datetime) -> tuple[float, str | None]:
    """Prefer a window you can realistically still build inside."""
    if not end_at:
        return 0.35, None
    days = (end_at - now).total_seconds() / 86400
    if days <= 0:
        return 0.0, None
    if days < 2:
        return 0.15, f"cuma {max(int(days * 24), 1)} jam tersisa"
    if days < IDEAL_MIN_DAYS:
        return 0.55, f"mepet — {int(days)} hari lagi"
    if days <= IDEAL_MAX_DAYS:
        return 1.0, f"waktu persiapan pas ({int(days)} hari)"
    # Still fine, just not urgent.
    return max(0.45, 1 - (days - IDEAL_MAX_DAYS) / 240), f"masih lama ({int(days)} hari)"


def _log_scale(value: int | None, ceiling: int) -> float:
    if not value or value <= 0:
        return 0.0
    return min(1.0, math.log10(value + 1) / math.log10(ceiling))


def score_row(row, profile: Profile, now: datetime | None = None) -> dict:
    """Score one candidate. Returns score 0-100 plus the reasons behind it."""
    now = now or utcnow()
    themes = json.loads(row["themes"] or "[]")
    end_at = parse_iso(row["end_at"])
    reasons: list[str] = []

    timing, timing_reason = _timing_score(end_at, now)
    # None means the source never published the figure; 0 means it published a
    # zero. Only the first is unknown, and only the first is dropped.
    has_prize = row["prize_amount"] is not None
    has_traction = row["participants"] is not None
    prize = _log_scale(row["prize_amount"], 100_000)
    traction = _log_scale(row["participants"], 10_000)

    if profile.is_cold:
        parts = [(COLD_TIMING, timing)]
        if has_prize:
            parts.append((COLD_PRIZE, prize))
        if has_traction:
            parts.append((COLD_TRACTION, traction))
        total = _blend(parts)
        if timing_reason:
            reasons.append(timing_reason)
        if row["prize_amount"]:
            reasons.append(f"hadiah ${row['prize_amount']:,}".replace(",", "."))
        if row["participants"]:
            reasons.append(f"{row['participants']:,} pendaftar".replace(",", "."))
        if not has_prize and not has_traction:
            reasons.append(f"{row['source']} tidak menerbitkan hadiah/jumlah pendaftar"
                           " — dinilai dari waktu saja")
        reasons.append("belum ada riwayat — peringkat dari sinyal umum")
        return {"score": round(total), "reasons": reasons[:4], "matched_themes": []}

    theme_total = sum(profile.themes.values()) or 1
    matched = [t for t in themes if t in profile.themes]
    theme_hit = sum(profile.themes[t] for t in matched) / theme_total
    theme = min(1.0, theme_hit * 2.2)  # a couple of strong matches is enough

    audience = profile.share(profile.audiences, row["audience"])
    mode = profile.share(profile.modes, row["mode"])
    source = profile.share(profile.sources, row["source"])

    prize_fit = prize
    median = profile.median_prize
    if median:
        # Reward being in the same league as what you usually enter.
        ratio = (row["prize_amount"] or 0) / median if median else 0
        prize_fit = max(prize, min(1.0, ratio / 2)) if ratio else prize * 0.5

    parts = [
        (W_THEME, theme), (W_AUDIENCE, audience), (W_MODE, mode),
        (W_SOURCE, source), (W_TIMING, timing),
    ]
    if has_prize:
        parts.append((W_PRIZE, prize_fit))
    if has_traction:
        parts.append((W_TRACTION, traction))
    total = _blend(parts)

    if matched:
        reasons.append("tema cocok: " + ", ".join(matched[:3]))
    if audience >= 0.5 and row["audience"]:
        label = "mahasiswa/pelajar" if row["audience"] == "student" else "umum"
        reasons.append(f"segmen {label}, sesuai kebiasaanmu")
    if mode >= 0.5 and row["mode"]:
        reasons.append(f"format {row['mode']}, sesuai kebiasaanmu")
    if timing_reason:
        reasons.append(timing_reason)
    if median and row["prize_amount"] and row["prize_amount"] >= median:
        reasons.append(f"hadiah ${row['prize_amount']:,} — di atas rata-ratamu".replace(",", "."))
    elif row["prize_amount"]:
        reasons.append(f"hadiah ${row['prize_amount']:,}".replace(",", "."))
    if traction >= 0.7 and row["participants"]:
        reasons.append(f"{row['participants']:,} pendaftar".replace(",", "."))

    return {"score": round(total), "reasons": reasons[:4], "matched_themes": matched}


def rank(candidates, profile: Profile, limit: int = 20, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    scored = []
    for row in candidates:
        result = score_row(row, profile, now)
        if result["score"] <= 0:
            continue
        scored.append((result, row))
    scored.sort(key=lambda pair: -pair[0]["score"])
    return scored[:limit]
