# DECISIONS.md

Architecture decision record for **IIT Khana**. Each decision records what we
chose, why, and what we rejected. Decisions made in Phase 0 are based on
reconnaissance of `campusmess.in` performed 2026-08-23; the raw findings live in
the (uncommitted) working notes.

Decisions marked **PROVISIONAL** depend on an open question and may change.

---

## D1. Data source: the public `campusmess.in` JSON API

**Decision.** Ingest from the REST API at `https://campusmess.in/api`. Do not
scrape HTML.

**Why.** `campusmess.in` is a Vite/React SPA; `GET /` returns a 2.4 KB shell with
an empty `<div id="root">` and no server-rendered content, so HTML scraping
yields nothing. Reading the JS bundle revealed an axios client with
`baseURL: "https://campusmess.in/api"`. Three endpoints answer **unauthenticated**:

| Endpoint | Gives us |
|---|---|
| `GET /api/halls` | roster of 14 halls with `id`, `name`, `type`, `location`, `tags`, `validFor` |
| `GET /api/halls/{id}/weekly` | the hall's current menu: 21 rows (7 days x 3 meals) |
| `GET /api/menus` | all 819 rows ever uploaded, across every revision |

All return the envelope `{success, statusCode, message, data}`.

**Rejected:**
- *HTML scraping / headless browser.* No content to scrape, and a browser is a
  heavy dependency for data already available as JSON.
- *Authenticating to reach `/api/menu-parents`, `/api/institutions`,
  `/api/announcements/list`* (all 401). We will not create an account or send
  credentials to a third-party service to obtain data we do not need. This costs
  us the revision→month mapping; see D4.

**Known fragility.** This API is undocumented and unversioned. We depend on the
endpoint paths, the response envelope, and the field names. Any of them can
change without notice. Mitigated by caching every raw response (D9).

---

## D2. Scope

**In scope.**
- All **14 IIT Kanpur halls** listed by `/api/halls`, all `isVisible: true`,
  all under `institutionId 66685367-54c2-4650-8ef5-0584e80efcb9`:
  GH 1, Hall 2, Hall 3, Hall 4, Hall 5, Hall 6, Hall 7, Hall 8, Hall 9,
  Hall 10, Hall 11, Hall 12, Hall 13, Hall 14.
  (There is no "Hall 1" in the source.)
- The **current menu revision only** — one weekly cycle per hall.
- Three meals: **Breakfast, Lunch, Dinner**. The source has no others.

**Explicitly out of scope.** No ordering. No reviews or ratings. No
authentication or user accounts. No personalization. No push notifications. No
write path of any kind back to `campusmess.in`. No admin, verification, or
crowd-review features (they exist in the upstream API; we ignore them).

**Why.** The project's deliverable is a benchmark and a results table comparing
a two-path router against naive RAG. Every feature above adds surface area
without adding a single benchmark question.

---

## D3. Date range: there is none — the menu is a weekly cycle

**Decision.** The unit of time is **`day_of_week`**, not `date`. Calendar dates
are resolved to a weekday at query time and never stored on a menu row.

**Why.** The source models menus as a repeating 7-day template. The menu row it
ships is literally `{hallId, dayOfWeek: "Monday", mealType: "Breakfast",
description, extras}`. There is no date field on any of the 819 rows. "What was
served on 12 June" is not answerable and never will be from this source; "what
is served on Mondays" is.

**Consequence for the brief.** The canonical row shape in the project brief was
`(hall, date, meal, item, tags, serving_window)`. `date` becomes `day_of_week`.
See D5 for `serving_window`, which has no source at all.

---

## D4. Historical revisions: archived raw, not exposed in v1

**Decision.** Persist all 819 rows from `/api/menus` as raw archive. Build the
queryable store from the **294 current rows** only.

**Why.** The 819 rows are 39 revisions of the weekly cycle (2–4 per hall), not a
dated archive. Halls carry month codes in `validFor` (`MAR-26`…`AUG-26`), but the
table mapping *revision → month* sits behind `/api/menu-parents`, which returns
401. So a superseded revision **cannot be truthfully labelled with a time
period**. A benchmark whose gold answers we cannot state confidently is worse
than no benchmark. We keep the bytes so a later phase can use them if the
mapping becomes available.

`/api/halls/{id}/weekly` was verified to return the highest `menuParentId` per
hall — i.e. the current menu. Currency varies: GH 1 was last updated 2026-08-09,
Hall 6 not since 2026-06-17.

**Rejected:** *treating `lastUpdated` as the menu's effective date.* It is an
upload timestamp, not a validity period; using it would fabricate precision.

---

## D5. Canonical data model

Two tables. One row per **item**, not per meal — the source ships comma-joined
strings and every interesting query is about items.

```sql
CREATE TABLE hall (
  id            INTEGER PRIMARY KEY,   -- upstream hall id; NOT the hall number
  name          TEXT NOT NULL,         -- "Hall 12", "GH 1"
  hall_type     TEXT,                  -- Boys | Girls | Co-ed
  location      TEXT
);

CREATE TABLE menu_item (
  hall_id       INTEGER NOT NULL REFERENCES hall(id),
  day_of_week   TEXT NOT NULL,         -- Monday..Sunday
  meal          TEXT NOT NULL,         -- Breakfast | Lunch | Dinner
  item          TEXT NOT NULL,         -- one dish, as written upstream
  item_norm     TEXT NOT NULL,         -- normalised for matching
  is_extra      INTEGER NOT NULL,      -- 0 = base menu, 1 = paid/optional extra
  tags          TEXT,                  -- derived: veg | non-veg | egg | dessert
  source_row_id INTEGER,               -- upstream menu row id, for traceability
  last_updated  TEXT                   -- upstream lastUpdated, ISO8601
);
```

Mapping from the brief's proposed shape:

| Brief | Here | Note |
|---|---|---|
| `hall` | `hall.name` | 14 values |
| `date` | `day_of_week` | **changed** — source has no dates (D3) |
| `meal` | `meal` | Breakfast/Lunch/Dinner |
| `item` | `item` | **derived** — source gives comma-joined strings, we split |
| `tags` | `tags` | **derived** — see below |
| `serving_window` | *(dropped)* | **no source exists** — see below |

### `item` is derived, and splitting is not trivial
The source gives `description` (base/veg menu) and `extras` (paid add-ons,
where the non-veg lives) as single comma-separated strings. Splitting on `,`
naively is wrong. Real cases from one hall alone:

- `Paratha (Aloo, Pyaz)` — comma inside parentheses, one item not two.
- `Kadhai Paneer / Paneer Butter Masala Or Chicken Kali Mirch (Non-Veg)` —
  alternatives joined by `/` and `Or`.
- `Poha, Jalebi, Curd Bhujia Namkeen (Mix)` — trailing qualifier.

The parser must respect parentheses and treat `/` and `Or` as alternatives.

### `tags` are derived from item text — never read from hall metadata
`/api/halls` exposes hall-level `tags` like `["Veg","Non-Veg"]`. **They are
unreliable.** Hall 3 has `tags: []`, implying no non-veg, while 10 of its 21
menu rows contain non-veg (`Kfc Chicken (Non-Veg)`, `Ghee Roast Chicken
(Non-Veg)`, …). The vocabulary is not even clean — Hall 12 says `Non-veg`,
everyone else `Non-Veg`.

Item text carries an inline `(Non-Veg)` marker, but **inconsistently**:
`Egg Biryani` carries no marker. So tagging is: inline marker first, then a
keyword lexicon (chicken, mutton, fish, egg, prawn…), then default veg.

### `item_norm` exists because the source spells things several ways
`sambhar` (27 occurrences) and `sambar` (7) are the same dish;
`nariyal chutney` (14) and `coconut chutney` (11) are the same thing in two
languages. Naive counting yields 995 "distinct" items from 1,617 mentions, which
overstates the real vocabulary. Normalisation + an alias table is required
before any aggregation query can be trusted.

### `serving_window` is dropped — **PROVISIONAL**
There is **no time data anywhere on campusmess.in**. Verified three ways: no key
containing "time"/"hour" on any of the 819 menu rows; no time-like string in any
harvested JSON except fragments of ISO timestamps; no timing field in the JS
bundle. Two plausible-looking mess notices in the bundle ("Mess dinner will run
from 7:00 PM to 9:45 PM…") are **hardcoded demo data**, sitting beside invented
contacts like `sameer.khan@hall14.edu`.

Consequence: the brief's example question *"which halls serve breakfast after
9?"* is **not answerable** and is removed from the benchmark. Alternatives
considered: hardcode a campus-wide default window (answerable, but identical for
all 14 halls, so it discriminates nothing); source timings from IITK's own hall
pages (a second ingestion path, outside the agreed data source). Neither is
adopted without a decision.

---

## D6. Storage: SQLite. A vector database is not justified.

**Decision.** One SQLite file for structured data. For the retrieval path, a
flat `numpy` array of embeddings with a brute-force cosine scan. No vector DB.

**Why — the numbers.**

```
14 halls x 7 days x 3 meals            =   294 menu rows
splitting description + extras         = 1,617 item mentions
                                       =   995 distinct raw strings (fewer after
                                             normalisation — see D5)
Raw JSON ingested for v1 (14 halls)    = 118 KB
including the full 819-row archive     = 480 KB
```

Under 2,000 embeddings. A brute-force cosine scan over a 1,617 x 1,024 float32
matrix is ~1.7 MB of RAM and sub-millisecond. An ANN index exists to avoid
scanning millions of vectors; here there is nothing to avoid. Introducing Qdrant,
pgvector, Chroma, or FAISS would add a service, a schema, a client library, and a
failure mode, in exchange for optimising away a cost that is already zero — and
would make the naive-RAG baseline harder to reproduce, which is the one thing
this project cannot afford.

**Rejected:** Qdrant / Chroma / pgvector / FAISS (all above); Postgres (SQLite
handles 294 rows and needs no server); Redis (nothing to cache at this size —
the whole DB is smaller than a typical Redis connection buffer).

**Also rejected: any distributed or microservice architecture.** This is a
single-process Python application reading a few hundred rows.

---

## D7. Two-path query router

**Decision.**

```
                    ┌─────────────────┐
   question ───────►│   classifier    │
                    └────────┬────────┘
                             │
              structured ◄───┴───► retrieval
                    │                  │
       ┌────────────▼──────┐   ┌───────▼─────────────┐
       │ SQL over SQLite   │   │ embed + cosine scan │
       │ filter/count/     │   │ over item text      │
       │ group/negate      │   │                     │
       └────────────┬──────┘   └───────┬─────────────┘
                    └────────┬─────────┘
                             ▼
                    answer synthesis (LLM)
```

**Why.** The project's whole claim is that most real mess questions are
**filters and aggregations over structured data**, and that top-k semantic
retrieval answers them wrongly *by construction*, not by accident:

- **Counting is impossible under top-k.** "How many halls serve paneer at dinner
  on Tuesday?" has answer 8/14. Any retriever with k < 14 cannot see all the
  evidence, so it cannot count. A `SELECT COUNT(*)` cannot get it wrong.
- **Negation inverts similarity.** "Which halls do NOT serve rice at lunch?"
  — an embedding of that question is *most* similar to chunks that talk about
  rice, i.e. exactly the wrong rows. SQL `NOT EXISTS` is exact.
- **Comparison needs two specific rows**, not the two nearest ones.

The retrieval path still earns its place for genuinely fuzzy questions —
"something spicy and North Indian", "is there anything sweet tonight" — where
there is no clean predicate to write.

**Rejected:**
- *Retrieval-only (naive RAG).* This is the **baseline we are measuring
  against**, not the design.
- *SQL-only.* Loses fuzzy questions, and loses the router, which is the thesis.
- *Agentic tool-calling loop over both.* Non-deterministic and much harder to
  attribute results to — when the point is a comparison table, we want to know
  exactly which path answered.

---

## D8. Benchmark: five query categories

The benchmark is hand-built with gold answers computed from the ingested data.
Every example below was **verified answerable** against real Phase-0 data.

| # | Category | Tests | Expected baseline behaviour |
|---|---|---|---|
| 1 | **Lookup** | single (hall, day, meal) fact | naive RAG should do *fine* — the honest control |
| 2 | **Comparison** | two named halls, same slot | RAG tends to return chunks from one hall only |
| 3 | **Aggregation** | count/group across all 14 halls | RAG structurally cannot count under top-k |
| 4 | **Negation / temporal** | absence, and date→weekday | embeddings retrieve the *presence* of the term |
| 5 | **Policy** | non-menu facts about halls | **weak category — see caveat** |

### Acceptance criteria — one verified question per category

**1. Lookup**
> *"What's for dinner at Hall 12 on Wednesday?"*
> Gold: `Kadhai Paneer / Egg Curry, Panchratan Dal`; extras `White Sauce Pasta,
> Besan Halwa, Mutton Rogan Josh (Non-Veg)`.

**2. Comparison**
> *"Is Friday lunch better at Hall 5 or Hall 4 if I want paneer?"*
> Gold: both serve paneer — Hall 5 has `Paneer 65`, Hall 4 has
> `Paneer-Do-Pyaza`, both in extras.

**3. Aggregation**
> *"How many halls serve paneer at dinner on Tuesday?"*
> Gold: **8 of 14** — GH 1, Hall 2, Hall 5, Hall 6, Hall 9, Hall 10, Hall 13,
> Hall 14.

**4. Negation / temporal**
> *"Which halls do NOT serve rice at lunch on Monday?"*
> Gold: **10 of 14** — Hall 2, Hall 4, Hall 5, Hall 6, Hall 8, Hall 9, Hall 10,
> Hall 11, Hall 12, Hall 14.
> Temporal variant: *"which mess has chicken for dinner tonight?"* — on a
> Tuesday the gold is **5 of 14** (GH 1, Hall 8, Hall 10, Hall 12, Hall 14).
> Tuesday is the discriminating day; Thursday and Sunday are 13/14 and make
> weak tests.

**5. Policy**
> *"Which halls are girls' halls, and do they serve non-veg?"*
> Gold: GH 1 and Hall 4; both serve non-veg (confirmed from item text, since
> hall `tags` are unreliable — D5).

> **Caveat, stated plainly.** Category 5 is answerable only from hall
> *metadata*, because **no policy prose exists** (D10). Questions students
> actually ask under "policy" — mess timings, guest meal rules, rebate
> procedure, extras pricing — are **not answerable from campusmess.in**.

### Why these are fair tests
The halls genuinely differ, so these questions have discriminating answers.
Measured: pairwise Jaccard similarity of item sets between every pair of halls
for the same (day, meal), n = 1,911 pairs — **mean 0.019, median 0.000, and 88%
of pairs share zero items.** If the 14 messes served near-identical food, no
filter question would discriminate and the benchmark would be vacuous. They
don't, so it isn't.

---

## D9. Scraping posture

- **Rate limit: 1 request/second**, enforced by a shared timestamp across
  process invocations, not just an in-loop sleep.
- **Cache everything.** Every response body and its headers are written to disk
  keyed by URL; a cached URL is never re-fetched. **Phase 0 recon cost 25 HTTP
  requests in total**, and that included the 1.4 MB JS bundle and ten dead-end
  probes.
- **Descriptive User-Agent** identifying the project and a contact address:
  `IIT-Khana/0.1 (student project; contact <email>)`. No browser impersonation.
- **Read-only.** `GET` only. We never call a write, auth, admin, or verification
  endpoint, and we never create an account.
- **Cheap by design.** The full dataset is 3 endpoints. A refresh is 16 requests
  (`/halls`, `/menus`, and 14 `weekly` calls) and need run no more than daily —
  menus change a few times a month.

**robots.txt: there isn't one.** `GET /robots.txt` returns HTTP 200 with the SPA
`index.html` (identical 2,427-byte body and ETag as `GET /`); nginx serves the
SPA for every unknown path. So no crawl directives are published — nothing is
disallowed, but nothing is permitted either. Our politeness is self-imposed
rather than compliance with a stated policy. (Side effect worth remembering:
**status codes cannot be used to test whether a path exists on this host** —
check `Content-Type` instead.)

**Maintainer contact: the maintainer was contacted and did not respond.**
We proceed on the basis that the data is publicly served without
authentication, rate limits, or crawl restrictions, that our access is read-only
and negligible in volume, and that `campusmess.in` is credited in the README. If
the maintainer objects, we stop and use cached data.

---

## D10. The retrieval path is PROVISIONAL — there is no prose corpus

**This is the most important open issue in the project. Recording it rather than
papering over it.**

The brief assumed a retrieval path over "prose documents". **Those documents do
not exist.** The complete prose content of `campusmess.in` is one announcement:

> **Request for latest menus** — "If anybody have access to the latest menu
> please upload it via rate button, or mail directly."

That is 21 words, and it is about the website, not about mess policy.
`/api/updates` returns `[]`. `/api/announcements/list` and `/api/goals` are 401
and 500. The app has no About, FAQ, or rules route. Every long English string in
the JS bundle is SaaS marketing copy, UI microcopy, a library warning, or
hardcoded demo data.

**Decision (PROVISIONAL): keep the two-path router, and let the retrieval path
run over menu item text rather than prose.** The router still has a real job —
route aggregations and negations to SQL, route fuzzy descriptive questions to
embeddings — and the naive-RAG baseline runs over the same rows, so the
comparison the project exists to make is fully intact and needs no new data.

**Alternatives, not adopted without a decision:**
- *Cut the retrieval path, ship one structured path.* Honest, but discards the
  router, which is the thesis.
- *Import a prose corpus from IITK sources* (hall rules, mess committee notices,
  rebate policy). Would give a real corpus and make category 5 meaningful, but
  it is a new data source, a new ingestion path, and a new source of truth.
  Candidate for a later phase.

---

## D11. Language and dependencies

Python. Dependencies are added per phase with a stated reason, never
speculatively. Phase 0 commits to `httpx`, `python-dotenv`, `pytest`. `sqlite3`
is stdlib. See D6 for what is deliberately absent.
