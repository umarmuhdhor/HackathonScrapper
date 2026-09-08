"""Pins for the parsing heuristics.

Everything here is a pure function fed by markup and JSON we do not control, so
these tests exist to make a regex tweak fail loudly instead of quietly changing
which hackathons you are shown. Each case that came from a real page carries the
URL it was taken from.
"""

from datetime import datetime, timedelta, timezone

from scraper.eligibility import UserProfile
from scraper.models import (
    classify_audience,
    humanize_deadline,
    parse_money,
    parse_period,
    parse_relative_deadline,
    resolve_country,
)
from scraper.recommend import Profile, score_row
from scraper.rules import RULES_MARKER_RE, parse_eligibility
from scraper.sources.lablab import normalize as lablab_normalize

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


# --------------------------------------------------------------- parse_money

def test_money_plain():
    assert parse_money("$<span data-currency-value>740,000</span>") == (740000, "USD")
    assert parse_money("€10.000") == (10000, "EUR")
    assert parse_money(None) == (None, None)
    assert parse_money("No prize") == (None, None)


def test_money_takes_first_amount_only():
    """'$1,000 + $500' used to concatenate into 1500000."""
    assert parse_money("$1,000 + $500") == (1000, "USD")


def test_money_drops_cents_instead_of_inflating():
    """'$12.50' used to become 1250."""
    assert parse_money("$12.50") == (12, "USD")


# -------------------------------------------------------------- parse_period

def test_period_shapes():
    assert parse_period("Jul 31 - Oct 01, 2026")[0] == datetime(2026, 7, 31, tzinfo=timezone.utc)
    # day-only right half inherits the left month
    assert parse_period("Sep 04 - 06, 2026")[1].day == 6
    # single date means a one-day event
    start, end = parse_period("Sep 09, 2026")
    assert start.day == end.day == 9
    # year rolls back when the range crosses December
    assert parse_period("Dec 15, 2025 - Jan 10, 2026")[0].year == 2025
    assert parse_period("Dec 31 - Jan 02, 2027")[0].year == 2026


def test_period_rejects_junk():
    assert parse_period("Feb 30, 2026") == (None, None)
    assert parse_period(None) == (None, None)


# ------------------------------------------------------------ deadline texts

def test_relative_deadline():
    assert parse_relative_deadline("25 days left", NOW) == NOW + timedelta(days=25)
    assert parse_relative_deadline("about 9 hours left", NOW) == NOW + timedelta(hours=9)
    assert parse_relative_deadline("ended", NOW) is None


def test_humanize_deadline():
    assert humanize_deadline(NOW + timedelta(days=12), NOW) == "12 days left"
    assert humanize_deadline(NOW - timedelta(hours=1), NOW) == "closed"


# ----------------------------------------------------------- resolve_country

def test_country_from_real_locations():
    """Locations taken verbatim from live Devpost and MLH payloads."""
    assert resolve_country("Waterloo, ON") == "CA"
    assert resolve_country("Waterloo, Ontario") == "CA"
    assert resolve_country("Boston, MA") == "US"
    assert resolve_country("Jakarta, Indonesia") == "ID"
    assert resolve_country("London, UK") == "GB"


def test_country_ignores_online():
    for text in ("Online", "Virtual", "Everywhere, Worldwide", None):
        assert resolve_country(text) is None


def test_country_does_not_read_the_word_on_as_ontario():
    """Bare province codes used to match as free words anywhere in the string,
    so any location containing "on" resolved to Canada. Unknown is the correct
    answer for these — eligibility then says "cek sendiri" rather than treating
    the event as foreign and hiding it."""
    for text in ("Innovation on Tour, Boston", "Hands-on Workshop", "Online, on demand"):
        assert resolve_country(text) != "CA"
    assert resolve_country("Hands-on Workshop") is None


# --------------------------------------------------------- audience guessing

def test_audience():
    assert classify_audience("mlh", "HackMIT") == "student"
    assert classify_audience("mlh", "Global Hack Week: AI") == "general"
    assert classify_audience("devpost", "Campus Innovation Challenge") == "student"
    assert classify_audience("devpost", "AI Agents Hackathon") == "general"


# ------------------------------------------------------- rules / eligibility

# Condensed from https://agentic-cinema.devpost.com/rules — the age-of-majority
# carve-out names Taiwan, and the real exclusion list follows further down.
AGENTIC_CINEMA = (
    "You are not eligible to receive the prizes described in these Rules unless you agree "
    "to these Rules. 2. SPONSOR: The Contest is sponsored by Google LLC located at 1600 "
    "Amphitheater Parkway, Mountain View, CA, 94043, USA. 4. ELIGIBILITY: To be eligible to "
    "enter the Contest, you must: (1) be above the age of majority in the country, state, "
    "province or jurisdiction of residence (or at least twenty years old in Taiwan) at the "
    "time of entry; (2) not be a resident of Italy, Brazil, Quebec, Crimea, Cuba, Iran, "
    "Syria, North Korea, Sudan, Belarus, Russia or the Crimea, Donetsk, and Luhansk regions "
    "of Ukraine, or Afghanistan, Antarctica, China, Djibouti, Iraq, Kazakhstan, Somalia, "
    "Venezuela, Vietnam, Western Sahara, and any other country designated by the United "
    "States Treasury's Office of Foreign Assets Control; (3) not be a party identified on "
    "OFAC's Specially Designated Nationals list."
)


def test_age_clause_country_is_not_an_exclusion():
    """Taiwan appears only in an age-of-majority carve-out."""
    assert "TW" not in parse_eligibility(AGENTIC_CINEMA)["excluded_countries"]


def test_real_exclusion_list_is_read_in_full():
    found = set(parse_eligibility(AGENTIC_CINEMA)["excluded_countries"])
    assert {"IT", "BR", "CU", "IR", "SY", "KP", "SD", "BY", "RU", "CN", "VE", "VN"} <= found


def test_employee_clause_countries_are_not_banned():
    """Sponsor-affiliate countries are not residency restrictions."""
    text = (
        "The Hackathon IS NOT open to: employees of the Sponsor, its affiliates in Germany "
        "and France, and their immediate family."
    )
    assert parse_eligibility(text)["excluded_countries"] == []


def test_plain_residency_ban_still_detected():
    result = parse_eligibility("The contest is not open to residents of Indonesia.")
    assert result["excluded_countries"] == ["ID"]


def test_student_only_detected():
    text = "You must be currently enrolled in an accredited university to participate."
    assert parse_eligibility(text)["requires_student"] is True


def test_openness_claim_detected():
    result = parse_eligibility("Open to everyone, anywhere in the world.")
    assert result["openness_claim"]
    assert result["excluded_countries"] == []


def test_submission_requirements_page_is_not_a_rules_page():
    """Real Devpost pages ('Project and Submission Requirements', 'Python
    Requirement') used to pass the gate and be recorded as verified."""
    for text in (
        "Project gallery Updates Discussions Project and Submission Requirements Track must "
        "be created primarily using BeatMind Original composition",
        "Hackathon Rules Python Requirement Python must play a meaningful role in your project.",
    ):
        assert not RULES_MARKER_RE.search(text)


def test_real_rules_page_still_passes_the_gate():
    assert RULES_MARKER_RE.search(AGENTIC_CINEMA)


# ------------------------------------------------------------------ lablab

def _lablab_raw(**extra):
    raw = {"slug": "x", "name": "Agent Week", "startAt": "2026-09-01T00:00:00Z",
           "endAt": "2026-09-20T00:00:00Z"}
    raw.update(extra)
    return raw


def test_missing_signup_flag_is_unknown_not_closed():
    """signup_open == 0 is a hard blocker, so an absent flag must stay None."""
    assert lablab_normalize(_lablab_raw()).signup_open is None
    assert lablab_normalize(_lablab_raw(signupActive=False)).signup_open == 0
    assert lablab_normalize(_lablab_raw(signupActive=True)).signup_open == 1


# ----------------------------------------------------------------- scoring

def _row(**extra):
    row = {"themes": "[]", "end_at": (NOW + timedelta(days=20)).isoformat(),
           "prize_amount": None, "participants": None, "audience": "general",
           "mode": "online", "source": "mlh"}
    row.update(extra)
    return row


def test_absent_prize_and_traction_do_not_score_as_zero():
    """MLH publishes neither figure; scoring them as 0 capped every MLH event at
    45/100 in cold start, so none could ever reach a daily top five."""
    assert score_row(_row(), Profile(), NOW)["score"] > 70


def test_published_zero_still_counts_against_a_row():
    """0 is a real value and must not be treated as missing data."""
    known_zero = score_row(_row(prize_amount=0, participants=0), Profile(), NOW)["score"]
    unknown = score_row(_row(), Profile(), NOW)["score"]
    assert known_zero < unknown


def test_full_evidence_outranks_thin_evidence():
    rich = score_row(_row(source="devpost", prize_amount=50000, participants=1200),
                     Profile(), NOW)["score"]
    assert rich > score_row(_row(), Profile(), NOW)["score"]


def test_ended_row_scores_zero():
    assert score_row(_row(end_at=(NOW - timedelta(days=1)).isoformat()), Profile(), NOW)["score"] == 0


# ----------------------------------------------------------------- profile

def test_profile_normalises_input():
    p = UserProfile.from_dict({"country": "id", "student_level": "undergrad"})
    assert p.country == "ID" and p.is_student is True
    assert UserProfile.from_dict({"travel": "bogus"}).travel == "online_only"
