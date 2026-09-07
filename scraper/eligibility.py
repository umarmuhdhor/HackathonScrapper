"""Can you actually enter this hackathon?

Nothing here is a substitute for reading a rules page. Only three real
eligibility signals exist across the sources — Devpost's `invite_only`, MLH's
venue country, and lablab's registration flag — everything else is inferred from
location and audience text. So the verdicts are deliberately three-way:

* ``blocked``  — a hard signal says you cannot enter (invite-only, registration
  closed, an onsite event you cannot travel to, a student-only event when you
  are not a student).
* ``check``    — something is probably restricted but we cannot prove it, most
  often a campus venue or a location string that resolves to no country.
* ``eligible`` — no rule fired. Not a guarantee, just nothing known against it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

CAMPUS_RE = re.compile(
    r"\b(university|universitas|college|campus|institute of technology|polytechnic"
    r"|politeknik|school of|univ\.)\b",
    re.I,
)
HIGHSCHOOL_RE = re.compile(r"\b(high school|highschool|secondary school|sma|smk|hs)\b", re.I)

TRAVEL_MODES = ("online_only", "domestic", "international")
# "recent_grad" covers fresh graduates and non-degree programs (bootcamps,
# Apple Developer Academy, and similar). They are neither clearly in nor clearly
# out of student-only events, so those become a warning rather than a blocker.
STUDENT_LEVELS = ("none", "highschool", "undergrad", "postgrad", "recent_grad")
ENROLLED_LEVELS = ("highschool", "undergrad", "postgrad")


@dataclass
class UserProfile:
    """Who you are, for eligibility purposes only."""

    country: str = "ID"                 # ISO-2 of where you live
    is_student: bool = False            # enrolled right now
    student_level: str = "none"         # none | highschool | undergrad | postgrad | recent_grad
    travel: str = "online_only"         # online_only | domestic | international
    allow_invite_only: bool = False     # keep invite-only events in the list anyway

    @classmethod
    def from_dict(cls, data: dict | None) -> "UserProfile":
        data = data or {}
        p = cls(
            country=(data.get("country") or "ID").upper()[:2],
            is_student=bool(data.get("is_student")),
            student_level=data.get("student_level") or "none",
            travel=data.get("travel") or "online_only",
            allow_invite_only=bool(data.get("allow_invite_only")),
        )
        if p.travel not in TRAVEL_MODES:
            p.travel = "online_only"
        if p.student_level not in STUDENT_LEVELS:
            p.student_level = "none"
        p.is_student = p.student_level in ENROLLED_LEVELS
        return p

    @property
    def student_adjacent(self) -> bool:
        """Fresh graduate or non-degree program — often accepted, never certain."""
        return self.student_level == "recent_grad"

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(row, profile: UserProfile) -> dict:
    """Return {verdict, blockers, warnings} for one hackathon row."""
    blockers: list[str] = []
    warnings: list[str] = []

    mode = row["mode"]
    onsite = mode in ("onsite", "hybrid")
    country = row["country"]

    # --- rules scraped off the event's own page -----------------------------
    excluded = row["excluded_countries"] if "excluded_countries" in row.keys() else None
    if excluded:
        try:
            codes = json.loads(excluded)
        except (json.JSONDecodeError, TypeError):
            codes = []
        if profile.country in codes:
            blockers.append(
                f"Aturannya melarang peserta dari {profile.country} "
                f"(daftar larangan: {', '.join(codes)})"
            )
    if "requires_student" in row.keys() and row["requires_student"] and not profile.is_student:
        if profile.student_adjacent:
            warnings.append("Aturannya menyebut peserta harus mahasiswa aktif — cek apakah fresh grad diterima")
        else:
            blockers.append("Aturannya mensyaratkan status mahasiswa aktif")

    # --- hard signals straight from the source -----------------------------
    if row["invite_only"] and not profile.allow_invite_only:
        note = row["eligibility_note"]
        blockers.append("Hanya lewat undangan" + (f" — {note}" if note else ""))
    if row["signup_open"] == 0:
        blockers.append("Pendaftaran sudah ditutup di platformnya")
    if row["status"] == "ended":
        blockers.append("Lombanya sudah selesai")

    # --- can you physically be there? --------------------------------------
    if onsite:
        where = "hadir langsung" if mode == "onsite" else "ada sesi tatap muka"
        if profile.travel == "online_only":
            blockers.append(f"Butuh {where}, sedangkan kamu memilih online saja")
        elif profile.travel == "domestic":
            if country and country != profile.country:
                blockers.append(f"Lokasinya di {country}, di luar {profile.country}")
            elif not country:
                warnings.append("Lokasi onsite tapi negaranya tidak terbaca — pastikan sendiri")
        elif not country:
            warnings.append("Lokasi onsite tapi negaranya tidak terbaca — pastikan sendiri")

    # --- who it is meant for -----------------------------------------------
    if row["audience"] == "student" and not profile.is_student:
        if profile.student_adjacent:
            warnings.append(
                "Ditandai untuk mahasiswa — banyak lomba menerima fresh grad (biasanya ≤12 bulan)"
                " atau peserta program non-gelar, tapi cek syaratnya"
            )
        else:
            blockers.append("Ditujukan untuk mahasiswa/pelajar")

    blob = " ".join(str(row[k] or "") for k in ("title", "organizer", "location"))
    if HIGHSCHOOL_RE.search(blob) and profile.student_level != "highschool":
        warnings.append("Sepertinya khusus siswa SMA")
    if onsite and CAMPUS_RE.search(blob):
        warnings.append("Digelar di kampus — sering diprioritaskan/khusus mahasiswa kampus itu")
    if row["source"] == "mlh" and profile.student_level == "highschool":
        warnings.append("MLH adalah liga antar-kampus, umumnya untuk mahasiswa")
    if row["source"] == "mlh" and profile.student_adjacent:
        warnings.append("MLH menerima mahasiswa dan umumnya lulusan ≤12 bulan — cek aturan event-nya")

    # Unchecked rules are not the same as clean rules — say so instead of
    # implying the event was vetted.
    keys = row.keys()
    rules_ok = ("rules_ok" in keys and row["rules_ok"] == 1)
    rules_tried = ("rules_checked_at" in keys and row["rules_checked_at"])
    if not blockers and row["source"] == "devpost" and not rules_ok:
        warnings.append(
            "Halaman aturannya gagal dibaca — batasan negara mungkin ada"
            if rules_tried else
            "Aturan detailnya belum diperiksa — batasan negara mungkin ada"
        )
    elif not blockers and row["source"] in ("lablab", "mlh") and not rules_ok:
        claim = row["openness_claim"] if "openness_claim" in keys else None
        if claim:
            warnings.append(
                "Penyelenggara menyebut terbuka global, tapi itu klaim di deskripsi —"
                " bukan halaman aturan resmi. Cek sendiri sebelum daftar"
            )
        elif rules_tried:
            warnings.append("Halaman lombanya tidak memuat aturan yang bisa dibaca — cek manual")
        else:
            warnings.append("Aturan sumber ini belum diperiksa — cek halaman lombanya")

    verdict = "blocked" if blockers else ("check" if warnings else "eligible")
    return {"verdict": verdict, "blockers": blockers, "warnings": warnings,
            "rules_checked": bool(rules_ok)}


def annotate(row, profile: UserProfile) -> dict:
    """Row as a dict with an `eligibility` key attached."""
    data = dict(row)
    if isinstance(data.get("themes"), str):
        try:
            data["themes"] = json.loads(data["themes"])
        except json.JSONDecodeError:
            data["themes"] = []
    data["eligibility"] = evaluate(row, profile)
    return data
