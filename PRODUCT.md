# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two people: the author and one friend. Each runs their own local copy of the
repository against their own `data/hackathons.db`, so every instance is
single-operator by design. There are no accounts, no auth boundary, and no
shared deployment. The eligibility profile is one global row in the `settings`
table, and that is correct rather than a limitation — each database belongs to
exactly one person.

The situation of use is a developer at their own machine, deciding which
hackathons are worth entering out of a feed that is mostly noise. The default
recorded profile is Indonesia, not a student, online-only.

## Product Purpose

Turn three public hackathon feeds into a short, argued shortlist of events worth
this person's time, and then hold the commitments they make.

Success is a ranked handful the operator agrees with, produced from a feed of
hundreds. In the current database, 351 stored events reduce to 33 that pass the
eligibility filter, and the daily digest reduces those to a top 5.

Three functions in priority order:

1. **Rank** — score and explain which events are worth entering.
2. **Screen** — remove what this person cannot enter, using proof where it exists.
3. **Track** — hold status, checklist, progress, and deadlines for what they joined.

## Positioning

Ranking is transparent and arguable. No source publishes ratings or
"similar events" data, so the score is a weighted heuristic over stored fields
and every score ships the reasons that produced it. A suggestion can be
disagreed with rather than merely trusted. Two modes: **cold start** uses only
neutral signals (preparation time, prize, registrant count); **profiled** uses
the operator's own board — the themes, audience, format, source, and prize range
they keep picking — as the profile.

Eligibility is three-way on purpose. Only three hard signals exist across the
sources (Devpost `invite_only`, MLH venue country, lablab registration flag);
everything else is inferred from location and audience text. So the verdicts are
`eligible` / `check` / `blocked`, and unreadable is recorded as unreadable, never
as clean.

## Operating Context

- macOS, run locally. `./run.sh` starts uvicorn on port 8765.
- Optional daily automation via **launchd** (not cron): `./install-daily.sh`
  scrapes all three sources, reads pending rules pages, builds the digest, and
  fires a macOS notification with the #1 pick.
- Digests written to `data/digests/` as `YYYY-MM-DD.{md,html,json}` plus
  `latest.html`.
- The dashboard reads SQLite only. Opening, filtering, or searching never
  touches the network.

## Capabilities and Constraints

Built and working:

- Three sources, each with a documented extraction path: Devpost public JSON
  feed, lablab.ai Next.js RSC payload, MLH Inertia.js page payload. No headless
  browser anywhere.
- SQLite store at `data/hackathons.db`; rows are upserted, never deleted, so
  finished events remain as archive and `first_seen_at` records first sighting.
- Cache TTL of 6 hours per source; re-scrape only on request or `--force`.
- Rules reading: one HTTP request per event, throttled 0.4s, results persisted so
  checked events are never re-read.
- Recommendation scoring with per-score reasons; like/dismiss feedback.
- Tracking board (6 statuses), agenda, checklist, progress, personal deadline,
  deadline notifications with an urgent count.
- FastAPI backend; vanilla JS dashboard (no build step) in `web/`.
- Six dashboard pages: Ringkasan, Papan Saya, Agenda, Rekomendasi, Jelajahi,
  Scraper.

Constraints that are properties of the sources, not bugs:

- No ratings, no "similar events", no outcome data exists on any source.
- lablab.ai publishes no rules text at all.
- MLH links to event sites that are mostly JavaScript-rendered and therefore
  unreadable; those are recorded as unreadable.
- Devpost is the only source with a uniform `/rules` page.

Planned, not built:

- **Bilingual UI.** Confirmed shape: interface chrome (nav, labels, buttons,
  empty states, errors) in Indonesian; scraped content (titles, themes, rules
  excerpts, locations) stays in its source English. No translation of scraped
  data. Currently the Indonesian strings are hardcoded across `web/index.html`
  and `web/app.js` with no message catalog.

## Brand Commitments

- Name: **Hackathon Screening**.
- Interface language: Indonesian, in the operator's own register — lowercase,
  direct, no marketing tone (`"paksa ambil ulang walau data masih segar"`,
  `"perlu dicek"`, `"tidak bisa diikuti"`).
- Copy states limits plainly rather than reassuring. The profile dialog says
  outright that some rules are not published in machine-readable form and that
  the operator should read the rules page before registering. Future copy keeps
  that honesty.

## Evidence on Hand

- Real data: 351 events in `data/hackathons.db` (372 KB), 54 currently running,
  33 passing the current eligibility profile.
- `tests/test_parsers.py` covers the three source parsers.
- `README.md` documents extraction method, scoring, eligibility verdicts, and
  API surface in detail; treat it as the authoritative product record alongside
  this file.
- Generated digests in `data/digests/`.

Absences future work must not fabricate: there are no users beyond the two
operators, no testimonials, no win/outcome history beyond what the operator
types into the board, and no accuracy benchmark for the eligibility verdicts.

## Product Principles

1. **Rank first, argue always.** The shortlist is the product, and every score
   carries the reasons that produced it.
2. **Never assert eligibility we cannot prove.** Unreadable stays unreadable;
   `check` is a real verdict, not a soft `eligible`.
3. **Stored beats fresh.** Reading the dashboard never hits the network.
   Scraping happens only when asked, and the cost of asking is visible.
4. **One operator, one database.** No accounts, no tenancy, no sharing layer.
   Two people means two copies.
5. **Subtraction is the value.** Hundreds in, a handful out. Any feature that
   grows the list without improving the cut is working against the product.

## Accessibility & Inclusion

No user-specific requirement was established. The interface currently meets
WCAG AA for body text: all foreground tokens measure ≥4.5:1 against every
surface they are used on (`--faint` #7f8c9d at 4.70:1 worst case on
`--panel-2`). Motion respects `prefers-reduced-motion`, and hover-only
affordances are gated behind `@media (hover: hover) and (pointer: fine)`.
Future work holds that floor.
