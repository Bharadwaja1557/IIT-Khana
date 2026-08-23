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
-- Implemented in db/schema.sql (Phase 1). Abridged here; see the file.
CREATE TABLE hall (
  id            INTEGER PRIMARY KEY,   -- upstream hall id; NOT the hall number
  name          TEXT NOT NULL UNIQUE,  -- "Hall 12", "GH 1"
  hall_type     TEXT,                  -- Boys | Girls | Co-ed
  location      TEXT
);

CREATE TABLE menu_item (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  hall_id         INTEGER NOT NULL REFERENCES hall(id),
  day_of_week     TEXT NOT NULL,       -- Monday..Sunday   (no date column: D3)
  meal            TEXT NOT NULL,       -- Breakfast | Lunch | Dinner
  item_raw        TEXT NOT NULL,       -- verbatim upstream; the tagger needs it
  item_normalized TEXT NOT NULL,       -- lowercased, depunctuated, parens dropped
  item_canonical  TEXT NOT NULL,       -- cluster representative (D17)
  tags            TEXT,                -- veg | egg | non-veg  (derived, C2)
  is_extra        INTEGER NOT NULL,    -- 0 = base menu, 1 = paid/optional extra
  position        INTEGER NOT NULL,    -- index within its field
  source_row_id   INTEGER,             -- upstream menu row id
  last_updated    TEXT,                -- upstream lastUpdated, ISO8601
  UNIQUE (hall_id, day_of_week, meal, is_extra, position)   -- idempotency key
);

-- One row per (hall, day, meal), present or not. Exists so the coverage report
-- can tell "empty" from "absent" — a menu_item-only schema cannot.
CREATE TABLE slot (
  hall_id     INTEGER NOT NULL REFERENCES hall(id),
  day_of_week TEXT NOT NULL,
  meal        TEXT NOT NULL,
  n_base      INTEGER NOT NULL,
  n_extra     INTEGER NOT NULL,
  status      TEXT NOT NULL,           -- present | empty | malformed
  note        TEXT,
  PRIMARY KEY (hall_id, day_of_week, meal)
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
| — | `item_canonical` | **added** — cluster representative, kept separate from `item_normalized` so clustering stays auditable (D17) |
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

### `serving_window` is dropped — **FINAL, see D11**
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
pages (a second ingestion path, outside the agreed data source). Neither was
adopted; the decision is recorded in full in **D11**, and a test asserts the
column does not exist.

---

## D6. Storage: SQLite. A vector database is not justified.

**Decision.** One SQLite file for structured data. For the retrieval path, a
flat `numpy` array of embeddings with a brute-force cosine scan. No vector DB.

**Why — the numbers.** Updated with the measured Phase 1 figures; the Phase 0
estimates were close but computed with a cruder splitter.

```
14 halls x 7 days x 3 meals            =   294 menu rows  (100% present)
after splitting description + extras   = 1,903 item rows
                                       =   931 distinct normalized strings
                                       =   776 distinct after clustering (D17)
Raw JSON ingested                      =   118 KB
Populated SQLite database              =   ~430 KB
```

Under 2,000 embeddings. A brute-force cosine scan over a 1,903 x 1,024 float32
matrix is ~7.8 MB of RAM and sub-millisecond. An ANN index exists to avoid
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

## D8. Benchmark: six query categories

Hand-built, with gold answers computed from the ingested database. Every
question below was **re-verified against `db/khana.db` after Phase 1 ingestion**
and passes the D15 discrimination gate (2–12 of 14 halls).

| # | Category | Tests | Expected baseline behaviour |
|---|---|---|---|
| 1 | **Lookup** | single (hall, day, meal) fact | naive RAG should do *fine* — the honest control |
| 2 | **Comparison** | two named halls, same slot | RAG tends to return chunks from one hall only |
| 3 | **Aggregation** | count/group across all 14 halls | RAG structurally cannot count under top-k |
| 4 | **Negation** | absence of a Group-2 item (D14) | embeddings retrieve the *presence* of the term |
| 5 | **Temporal** | "tonight"/"today" → weekday, then filter | baselines have no date grounding |
| 6 | **Fuzzy semantic** | no writable predicate (D13) | **SQL fails here**; this is retrieval's home ground |
| 7 | **Policy** | non-menu facts about halls | **near-empty — see caveat** |

Negation and temporal were one category in Phase 0. They are split because they
fail differently: negation breaks embedding similarity, temporal breaks date
grounding. Numbering above is presentation order; the six *scored* categories
are lookup, comparison, aggregation, negation/temporal, fuzzy semantic, policy.

### Acceptance criteria — one verified question per category

**1. Lookup** — gold: 1 hall
> *"What's for dinner at Hall 12 on Wednesday?"*
> Gold: base `Kadhai Paneer`, `Egg Curry`, `Panchratan Dal`;
> extras `White Sauce Pasta`, `Besan Halwa`, `Mutton Rogan Josh (Non-Veg)`.

**2. Comparison** — gold: 2 halls
> *"Is Friday lunch better at Hall 5 or Hall 4 if I want paneer?"*
> Gold: both serve paneer, both in extras — Hall 5 `Paneer 65`,
> Hall 4 `Paneer-Do-Pyaza`. Hall 5 also has `Saag Chicken (Non-Veg)`;
> Hall 4 also has `Chicken-Do-Pyaza (Non-Veg)`.

**3. Aggregation** — gold: **8 of 14** ✓ in band
> *"How many halls serve paneer at dinner on Tuesday?"*
> Gold: GH 1, Hall 2, Hall 5, Hall 6, Hall 9, Hall 10, Hall 13, Hall 14.

**4. Negation** — gold: **9 of 14** ✓ in band
> *"Which halls do NOT serve chicken at dinner on Tuesday?"*
> Gold: Hall 2, Hall 3, Hall 4, Hall 5, Hall 6, Hall 7, Hall 9, Hall 11,
> Hall 13.
>
> *Replaces Phase 0's "which halls do NOT serve rice at lunch on Monday"
> (gold 10 of 14), which is **withdrawn**: rice is an assumed-present staple
> (D14), so that question measured transcription completeness, not food.
> Chicken is a Group-2 meaningful-absence item — a hall serving chicken
> writes it down.*

**5. Temporal** — gold: **5 of 14** ✓ in band
> *"Which mess has chicken for dinner tonight?"* (asked on a Tuesday)
> Gold: GH 1, Hall 8, Hall 10, Hall 12, Hall 14.
> Requires resolving "tonight" → Tuesday → Dinner before filtering. Tuesday is
> the discriminating day; **Thursday (13/14) and Sunday (13/14) fail the D15
> gate** and may not be used.
>
> *Replaces Phase 0's "which halls serve breakfast after 9?", which is
> **withdrawn** as unanswerable — no serving-time data exists (D11). The
> date→weekday grounding this was really testing survives intact.*

**6. Fuzzy semantic** — gold: **7 of 14** ✓ in band
> *"Which halls do a South-Indian style breakfast on Sunday?"*
> Gold: Hall 2, Hall 3, Hall 5, Hall 10, Hall 12, Hall 13, Hall 14.
> No item contains the string "South Indian". The predicate does not exist in
> the corpus; the answer must come from meaning (`dosa`, `idli`, `uttapam`,
> `vada`, `sambhar`, `upma`). `LIKE '%south indian%'` returns zero rows.

**7. Policy** — gold: 2 halls
> *"Which halls are girls' halls, and do they serve non-veg?"*
> Gold: GH 1 and Hall 4; both serve non-veg — confirmed from item text, since
> hall-level `tags` are unreliable (D5: Hall 3 declares `tags: []` while serving
> non-veg in 10 of 21 slots).

> **Caveat, stated plainly.** Category 7 is answerable only from hall
> *metadata* (`hall_type`, `location`), because **no policy prose exists**
> (D10). Questions students actually ask under "policy" — mess timings, guest
> meal rules, rebate procedure, extras pricing — are **not answerable from
> campusmess.in**. Per D13 this category is reported at whatever size it
> honestly reaches and is not padded.

### Why these are fair tests

Every question above names between 2 and 12 of 14 halls, so no system can score
by answering "all of them" or "none". See D15 for the gate and D16 for what a
correct answer does and does not claim.

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

## D10. The retrieval path runs over menu item text (FINAL)

*Phase 0 recorded this as PROVISIONAL. Phase 1 makes it final.*

The brief assumed a retrieval path over "prose documents". **Those documents do
not exist.** The complete prose content of `campusmess.in` is one announcement:

> **Request for latest menus** — "If anybody have access to the latest menu
> please upload it via rate button, or mail directly."

That is 21 words, and it is about the website, not about mess policy.
`/api/updates` returns `[]`. `/api/announcements/list` and `/api/goals` are 401
and 500. The app has no About, FAQ, or rules route. Every long English string in
the JS bundle is SaaS marketing copy, UI microcopy, a library warning, or
hardcoded demo data.

**Decision: the retrieval path runs over menu item text. There is one corpus,
not two.**

**What this changes about the router.** It is no longer routing *between
corpora* — it routes **between methods over a single corpus**. That is a weaker
claim than the brief started with, and stating it plainly matters, because the
router now has to earn its place on method fit alone rather than on having
privileged access to documents SQL cannot see.

**It does earn it, on fuzzy-intent queries.** Questions like *"something
spicy"*, *"anything like a dosa"*, *"a light dinner"* have no predicate to
write: `LIKE '%spicy%'` returns nothing, because no item in the corpus contains
the word "spicy". There is no `is_spicy` column and there will not be one.
Embeddings over item text do return `Chilli Chicken`, `Honey Chilli Potato`,
`Achari Kaddu`. Conversely SQL wins outright on counting and negation, where
top-k retrieval fails structurally (D7). Each path answers a class the other
cannot, which is what makes routing between them a real decision rather than a
coin toss. This is why D13 adds an explicit **fuzzy semantic** benchmark
category: without it, the retrieval path is never exercised on its home ground
and the router looks like dead weight.

**Rejected: importing a prose corpus from IITK pages** (hall rules, mess
committee notices, rebate policy). It would make the "policy" category
meaningful, but it is a different data source, a different ingestion path, a
different source of truth, and different scraping ethics. **It belongs to a
different project.** Adding it here would replace a sharp, measurable question
("does routing beat naive RAG on structured data?") with a vague one.

---

## D11. `serving_window` is dropped (FINAL)

*Supersedes the PROVISIONAL note in D5.*

**Decision: there is no `serving_window` column, and no serving-time data in
this project.**

**Why.** campusmess.in has no time data at all. Verified three ways in Phase 0:
no key containing "time" or "hour" on any of the 819 upstream menu rows; no
time-like string in any harvested JSON except fragments of ISO `lastUpdated`
timestamps; no timing field anywhere in the 1.4 MB JS bundle. The two
plausible-looking mess notices in the bundle ("Mess dinner will run from 7:00 PM
to 9:45 PM…") are hardcoded demo data sitting beside invented contacts like
`sameer.khan@hall14.edu`.

`tests/test_load.py::test_no_date_or_serving_window_columns` asserts the column
does not exist, so a later phase cannot quietly reintroduce it.

**The acceptance question "which halls serve breakfast after 9?" is removed.**
It is unanswerable. It is replaced by a different temporal question (D8,
category 5), because the thing that question was really testing — resolving
"tonight" / "today" to a weekday before querying — is still worth testing and
does not need clock times to test it.

**Rejected: hardcoding a campus-wide default window** (e.g. breakfast
07:30–09:30). It would make the question answerable, but the answer would be
identical for all 14 halls, so it discriminates nothing and would fail the D15
gate anyway. Inventing uniform data to satisfy a question is worse than
dropping the question.

---

## D12. The benchmark compares THREE systems, not two

**Decision.** Every benchmark run evaluates:

1. **Naive RAG** — embed the question, top-k over menu item chunks, stuff into
   the prompt. The strawman baseline.
2. **Long-context stuffing** — the entire 294-row menu in the prompt, roughly
   25k tokens. No retrieval at all.
3. **The structured router** — classify, then SQL or retrieval (D7, D10).

**Why the second one has to be there.** The corpus is small enough that a
sceptic will immediately ask why not just put the whole menu in the prompt, and
at 294 rows they are half right. If the project only beats naive RAG, it has
beaten a strawman it chose itself. Long-context stuffing is the honest,
strong baseline, and it is what a competent engineer would actually build first.

**Every result reports three numbers per category, not one:**

    accuracy   |   tokens per query   |   p50 latency

Accuracy alone hides the trade. It is entirely possible — and should be reported
plainly if it happens — that stuffing **wins on accuracy** while costing 50x the
tokens and several times the latency. That is a real finding about when routing
is worth building, not a failure. The result this project is allowed to claim is
whatever those three columns actually say.

---

## D13. Sixth benchmark category: fuzzy semantic

**Decision.** The benchmark covers six categories:

    1. lookup            4. negation / temporal
    2. comparison        5. fuzzy semantic   (NEW)
    3. aggregation       6. policy

**Fuzzy semantic** covers questions with no writable predicate — *"something
spicy for dinner"*, *"anything like a dosa"*, *"a light breakfast"*, *"is there
anything sweet tonight"*. SQL `LIKE` returns nothing for these because the
corpus contains dishes, not adjectives. This is the retrieval path's home
ground, and without the category the router's second path is never tested on
anything it is actually better at (D10).

**Warning on category 6, stated rather than papered over: "policy" is nearly
empty.** With no prose corpus, the only non-menu facts available are hall
metadata — `hall_type` (Boys / Girls / Co-ed) and `location`. That supports a
handful of genuine questions and no more. **The category will be reported at
whatever size it honestly reaches, even if that is three questions.** It will
not be padded with invented questions to look balanced, and if it ends up too
thin to measure, the results table will say "insufficient" rather than showing a
number computed from noise.

---

## D14. Vocabulary partition: assumed-present staples vs meaningful absence

**Decision.** Every item is classified into one of two groups. **Negation
questions may only use meaningful-absence items.**

### Why this exists

Halls transcribe only what *varies*. Hall 7 lists rice and roti in 14/14
lunch+dinner slots; every other hall lists them in 3–8/14. Side by side on a
Monday lunch:

    Hall 7  : Arhar Dal Tadka, Aloo Simla, Jeera-Rice, Fruits/Curd/Boondi Raita,
              Green Chatney, Chapati (Plain & Deshi Ghee), Plain-Rice
    Hall 2  : Red Masoor, Mix-Veg, Chhach

Hall 2 obviously serves chapati. It just does not write it down. **Absence in
the data does not imply absence at the mess.** The clinching evidence: *no hall
in the corpus lists drinking water even once*, and all 14 obviously serve it.

### Group 1 — assumed-present staples (absence proves NOTHING)

Exact normalized forms present in the corpus, with mention counts:

    rice (plain)     plain rice (13)
                     [reserved, not yet seen: rice, chawal, steamed rice, boiled rice]
    roti / chapati   chapati (12), roti (3), plain roti (2)
                     [reserved: chapatti, phulka]
    generic dal      mix dal (7), mix daal (3), dal fry (2), dal tadka (2),
                     mixed dal (1)
                     [reserved: dal, daal, plain dal]
    curd / dairy     curd (36), buttermilk (4), raita (4), dahi (3),
                     chaach (2), chhanch (2), chhach (1)
                     [reserved: mattha, plain curd]
    water            NONE PRESENT — zero occurrences across all 1,903 items

**Rule: the *plain, unmodified* form of a staple category is assumed-present. A
*named preparation* is not.** `mix dal` is a staple; `dal makhani`, `arhar dal`,
`panchratan dal` are named dishes whose absence is meaningful. `plain rice` is a
staple; `jeera rice`, `pulao`, `veg biryani` are not. `curd` is a staple;
`boondi raita`, `dahi vada` are not.

**Residual risk, recorded honestly:** a hall that serves arhar dal but writes
only "dal" would be missed by a question about arhar dal. This is a real and
unfixable source of error given the source data. It is bounded — only 15 of
1,903 mentions are generic dal forms — but it is not zero.

### Group 2 — meaningful-absence items (absence is informative)

Anything not in Group 1. Four sub-kinds, with corpus examples:

    named proteins    Chicken Biryani, Saag Chicken, Mustard Fish, Dahi Katla,
                      Mutton Rogan Josh, Egg Curry, Paneer 65, Chilli Paneer,
                      Kadhai Paneer, Fish Tikka, Chicken Lolipop
    named sweets      Gulab Jamun, Jalebi, Kheer, Besan Halwa, Kalakand,
                      Milk Cake, Shahi Tukda, Ras Malai, Ice Cream, Rabdi
    regional dishes   Rajma, Sambhar, Idli, Dosa, Uttapam, Poha, Chola,
                      Litti Chokha, Aloo Chokha, Amritsari Chhole, Achari Kaddu
    composite/branded French Fries, Honey Chilli Potato, Hakka Noodles,
                      White Sauce Pasta, Veg Momos, Manchurian, Aloo Tikki Chaat

### Consequence

Phase 0's negation acceptance question — *"Which halls do NOT serve rice at
lunch on Monday?"*, gold 10 of 14 — **is withdrawn.** Rice is a Group 1 staple,
so that question measured transcription completeness rather than food. It is
replaced in D8 with a Group 2 question.

---

## D15. Discrimination gate

**Decision.** Every benchmark question, in every category, must have a gold
answer naming **between 2 and 12 of the 14 halls inclusive**. Questions outside
that band are rejected as vacuous. Enforced programmatically in Phase 3; no
question enters the benchmark without passing.

**Why.** Halls overlap heavily on food *categories* while barely overlapping on
specific *dishes*. Measured: 84% of lunch slots and 85% of dinner slots contain
some dal, and on Tuesday and Wednesday it is 14/14 — yet mean pairwise Jaccard
over specific items is 0.034. So a question can look discriminating and be
worthless:

    "Which halls serve dal at lunch on Tuesday?"        14/14  -> REJECT
    "Which halls serve chicken at dinner on Thursday?"  13/14  -> REJECT
    "Which halls serve egg at breakfast on Wednesday?"   0/14  -> REJECT
    "Which halls serve chicken at dinner on Tuesday?"    5/14  -> pass
    "Which halls serve fish at lunch on Friday?"         2/14  -> pass

A question every system answers "all of them" to measures nothing; neither does
one whose answer is "none". Phase 0's own examples ranged from 5/14 (a good
test) to 13/14 (nearly vacuous) with no gate to catch the difference.

**Rejected:** hand-judging discrimination per question. It is exactly the kind of
thing that drifts under deadline pressure, and the gate costs one SQL query.

---

## D16. What this benchmark actually measures

**Decision. This benchmark measures faithfulness to the corpus, not truth about
IIT Kanpur dining.** Stated here, and to be stated in the README and alongside
any published results table.

A correct answer is one that correctly reflects what campusmess.in records. It
is **not** a claim about what a hall actually serves. The two differ, and the
gap is measurable, not hypothetical:

- Halls transcribe only what varies, so staples are systematically missing
  (D14). No hall lists drinking water; all serve it.
- Transcription density varies nearly 2x across halls (Hall 7 at 9.3 items per
  slot, Hall 3 at 4.6).
- Menu currency varies from 2026-06-17 (Hall 6) to 2026-08-09 (GH 1).
- All 294 current rows are upstream `status: "draft"` — none are "approved".

**Consequence for the Phase 1 Part A finding.** Mean pairwise Jaccard of ~0.034
supports **"the menus recorded for these halls differ"**. It does **not**
support "these halls serve different food", and no analysis of this corpus can,
because a hall that serves rice and does not list it is indistinguishable from
one that does not serve rice. Phase 0's headline that "88% of hall pairs share
zero items" is **retired**: the zero-fraction is confounded by transcription
density (it falls from 86% to 50% as menus get longer), while the mean is flat
in menu length (Pearson r = -0.001) and is the defensible statistic.

This does not weaken the project. The thesis is about **query methods**, and
comparing three systems against the same corpus is a fair test regardless of how
faithfully that corpus mirrors the messes.

---

## D17. Clustering never merges across diet lines

**Decision.** Two item strings are never placed in the same cluster if their
**diet signatures** differ, where a diet signature is the set of
veg / egg / non-veg markers present in the string. Implemented as a rule in
`ingestion/parse.py::build_clusters`, not as a blacklist of known-bad pairs.

**Why.** Fuzzy clustering merges spelling variants, which the corpus badly needs
(`sambhar`/`sambar`, `arhar dal`/`arhar daal`/`arhad dal`,
`dal makhani`/`dal makhni`/`daal makhni`). But `veg biryani` and `egg biryani`
are **one character apart**, so every edit-distance metric merges them. That
single merge would silently corrupt the veg/non-veg answer for a dish appearing
13 times, and it would corrupt the tagger's training and evaluation data at the
same time.

Blacklisting the biryani pair was rejected: the same collision applies to
`veg cutlet`/`egg cutlet`, `veg roll`/`egg roll`, `veg fried rice`/
`chicken fried rice`, and any future pair. The general rule costs one set
comparison per candidate merge.

**Implementation notes.** The signature is computed on the string as written,
including parentheticals, because two tag-critical strings hide their signal
there: `Banana Shake (Instead Of Milk, Banana And Egg)` and
`Veg & Chicken Momos (Non-Veg)`. The marker vocabulary deliberately excludes
preparation styles — `tikka`, `kebab`, `lollipop`, `65` — because they cut
across diets (`Paneer Tikka` and `Veg Kabab` are vegetarian). Four tests cover
this, including that `paneer tikka` reads as veg.

The guard is a **merge veto, not a classifier**. The Phase 1 C2 tagger has its
own lexicon, built against hand labels and measured; this is not that lexicon
and is not evaluated as one.

---

## D18. Ingestion reads the Phase 0 cache, not the network

**Decision.** `python -m scripts.ingest` reads cached API responses from
`.notes/recon/` (override with `RAW_CACHE_DIR`). It never makes a network
request.

**Why.** The benchmark must be reproducible against a fixed snapshot. An
ingestion that re-fetches would let upstream edits silently change gold answers
between runs, which would make the results table meaningless. It also honours
the rate-limit posture in D9 for free: zero requests.

**Known wart, recorded rather than hidden.** `.notes/` is gitignored, so a fresh
clone cannot ingest until the Phase 0 fetch is re-run. The raw JSON is 118 KB
and pinning it beside the benchmark would fix this. Deferred to the phase that
freezes the benchmark; noted here so it is not forgotten.

---

## D19. Language and dependencies

Python. Dependencies are added per phase with a stated reason, never
speculatively. Phase 0 committed to `httpx`, `python-dotenv`, `pytest`.
**Phase 1 added nothing** — parsing, normalisation, fuzzy clustering and storage
are all stdlib (`sqlite3`, `difflib`, `re`, `csv`). See D6 for what is
deliberately absent.
