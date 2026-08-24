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
**Phase 1 added nothing** — parsing, normalisation, fuzzy clustering, the diet
tagger and storage are all stdlib (`sqlite3`, `difflib`, `re`, `csv`). See D6 for what is
deliberately absent.

---

## D20. The tagger's error ceiling on tag-filtered questions

**Decision.** Diet tags (`veg` / `egg` / `nonveg` / `unclear`) are derived from
item text by a rule/keyword tagger (`ingestion/tagger.py`), populated at ingest.
Its error rate is a **hard ceiling on every tag-filtered benchmark question,
regardless of retrieval architecture.** No router, no embedding model and no
amount of prompt engineering can answer "which halls have chicken tonight" more
accurately than the tags underneath it.

### The headline metric is per-class precision and recall, NOT accuracy

**Superseded framing.** An earlier version of this decision quoted overall
accuracy on the unmarked subset (97.0%) as the ceiling, and noted that the
majority-class baseline beat it (98.5%). That framing was wrong, and it was
wrong in a way that flattered a useless classifier.

The corpus is 86.8% veg. On a distribution that skewed, accuracy is
near-uninformative: **"always answer veg" scores 98.5% on the unmarked subset
and has exactly zero non-veg recall.** Every chicken question returns zero
halls. It is a perfect score at the only job that does not matter, and total
failure at the only job that does.

So the headline is **per-class precision and recall**, and specifically:

> **The stated ceiling on tag-filtered questions is the tagger's ability to
> infer "this is meat" from a string carrying no `(Non-Veg)` marker.**

Accuracy is demoted to a footnote and should not be quoted as a headline result.

That quantity was intended to be expressed as non-veg recall on unmarked text.
C3 measured it and found the recall denominator is 1, so it is reported as
**precision plus bounded exposure** instead of a recall percentage. The next
section gives the numbers and the reasoning.

### The ceiling, as measured by the C3 adversarial pass

The random 150 could not measure this (all 17 of its non-veg labels were
marked; the unmarked subset had n=0 non-veg). C3 built a deliberately
adversarial sample of 91 items — `eval/labels/items_adversarial.csv`, scored
separately by `scripts/eval_adversarial.py`, **never pooled with the random
150** — to reach the 0.6% of rows that random sampling cannot.

    bucket                                    n    gold
    A  unmarked, tagger said nonveg          11    nonveg=11
    B  tagger abstained (unclear)            23    veg=22, nonveg=1
    C  unmarked, tagger said veg,            57    veg=56, egg=1
       ambiguous dish form

**Result 1 — precision on unmarked non-veg assertions: 100% (11/11), zero false
positives.** When the tagger asserts "this is meat" from text alone, with no
marker to lean on, it has not yet been wrong. This is a genuine measured number.

**Result 2 — non-veg recall: NOT ESTIMABLE.** Buckets B and C were selected
independently of what the tagger predicted, so they are the only valid recall
probe. Across all 80 of them the pass found **exactly one** true non-veg item:

    [MISSED, pred=unclear]  'Kolkatta Biryani'  x1 row

A denominator of 1 does not support a recall percentage. Quoting "0%" from n=1
would be worse than useless, so it is not quoted. **The ceiling is stated as
bounded exposure instead:**

> Of the 12 unmarked non-veg rows now known to exist in the corpus, the tagger
> tags 11 `nonveg` and **abstains** on the 12th. Zero are silently mislabelled
> as veg. Unmarked non-veg is **12 of 1903 rows = 0.63%**.

This is a near-census of the plausible hiding places rather than a sample
estimate, and it is deliberately not written as a recall figure — the numerator
of any such figure would include bucket A, which was selected by the tagger's
own predictions and is therefore circular for recall.

**Result 3 — bucket C returned zero non-veg, and that is the finding.** 57
items selected purely on ambiguous dish form (`biryani`, `pulao`, `roll`,
`kofta`, `do pyaza`, `fried rice`, `soup`, ... chosen independently of the
tagger's own vocabulary), and not one hid meat. **Unmarked non-veg in this
corpus is confined to items the tagger already catches.** That is a real result
about the corpus, not a failed experiment, and no further probe is warranted.

**The one silent error in 91 items.** `Nargisi Kofta Curry` is gold `egg`;
the tagger said `veg`. Nargisi kofta is built around a boiled egg, which is
recoverable only from culinary knowledge — the string contains no egg word.
This is D21 territory: a genuine limitation of a string-only method, not a rule
that should be patched in. Per-class over the adversarial set, **marked
biased-sample, context only, not generalization figures**:

    class      precision    recall   gold n   pred n
    veg            98.2%     71.8%       78       57
    egg              n/a      0.0%        1        0
    nonveg        100.0%     91.7%       12       11
    unclear         0.0%       n/a        0       23

Of 24 disagreements, **23 are abstentions and 1 is a wrong tag.** The tagger's
failure mode is visible uncertainty, not silent corruption — including on the
one non-veg it missed, where it abstained rather than asserting veg. That is the
behaviour D20 was designed for, and it held under adversarial sampling.

**Residual risk, stated rather than closed.** An unmarked non-veg item whose
name contains neither a lexicon protein nor an ambiguous dish form (a bare
`Kosha Mangsho`, say) would be missed by the tagger *and* by this probe. Nothing
in C3 rules that out. Exposure is bounded by the 0.63% figure only for the
hiding places actually searched. Not pursued further: at this corpus size the
measurement changes no downstream decision.

### Footnote: accuracy figures, retained for completeness

Measured against 150 random hand labels covering 534 mentions. Not the headline;
see above for why.

    evaluation                      n  accuracy   abstain  majority
    mention-level (all)           534     98.9%      1.1%     91.4%
    type-level (all)              150     97.3%      2.7%     87.3%
    marked subset                  17    100.0%      0.0%    100.0%
    unmarked subset               133     97.0%      3.0%     98.5%
    unmarked, mention-weighted    500     98.8%      1.2%     97.6%

Per class (type-level), and the confusion matrix with `unclear` retained as a
predicted class with an empty gold row:

    class      precision   recall     F1   support
    veg           100.0%    96.9%  98.4%       131
    egg           100.0%   100.0% 100.0%         2
    nonveg        100.0%   100.0% 100.0%        17
    unclear         0.0%      n/a    n/a         0

                    veg      egg   nonveg  unclear    total
    veg             127        0        0        4      131
    egg               0        2        0        0        2
    nonveg            0        0       17        0       17
    unclear           0        0        0        0        0

Read these with the caveat that `nonveg` precision/recall of 100% is measured
entirely on marked items and therefore measures regex matching, not inference.

### Abstention

The tagger emits `unclear` rather than guessing on genuinely ambiguous dish
names. A wrong tag silently corrupts a query answer; an abstention is visible.
Corpus-wide abstention rate: **34 of 1903 rows (1.8%)** — dominated by bare
`Pulao`, `Hakka Noodles`, `Bombay Sandwich`, `Burger`, `Noodles`, `Kathi Roll`,
`Kolkatta Biryani`, `Cutlet`, `Momos`, `Manchurian`. Abstention rate is always
reported **separately** from accuracy so the two are never conflated.

Full corpus distribution: veg 1652 (86.8%), nonveg 195 (10.2%),
unclear 34 (1.8%), egg 22 (1.2%).

### Change protocol: inspection-driven fixes are permitted, tuning is not

A fix found by **inspecting the corpus or the rules** is permitted. A fix found
by **looking at eval errors** is tuning and is not. The test is procedural and
is applied every time:

> Run the eval before and after. If the output is byte-identical, the change was
> not tuning. If it changed, report both numbers and name the rows that moved.

Applied three times so far:

1. **Egg before marker** (C2). Found by the cluster-consistency check. Upstream
   writes `Egg Curry (Non-Veg)`; its marker means "not vegetarian" two-class, so
   checking it first collapsed `egg` into `nonveg`. Egg now precedes the marker,
   after named proteins. Eval **byte-identical**; 5 DB rows changed, 0 eval rows.
2. **`murgh` added to the protein lexicon** (C2). Same scan; `\bmurg\b` does
   not match "Murgh". Eval **byte-identical**.
3. **Fuzzy tolerance on veg qualifiers** (C3). Found by a near-miss scan over
   all 572 corpus tokens. `Vegetgable Pulav` abstained on a transcription typo.
   Eval **byte-identical**; **1 DB row changed** (`unclear` -> `veg`).

### Fuzzy matching applies to veg qualifiers ONLY, permanently

The same near-miss scan run against the **protein/egg** lexicon returns 8 hits
at ratio >= 0.80, and 6 are catastrophic:

    'katli'  ~ katla   -> "Kaju Katli" (a sweet)          would become nonveg
    'kheera' ~ kheema  -> "Kheera Raita" (cucumber)       would become nonveg
    'kala'   ~ katla   -> "Kala Chana Curry"              would become nonveg
    'cham'   ~ ham     -> "Malai Cham Cham" (a sweet)     would become nonveg
    'and'    ~ anda    -> "Besan And Moong Dal Chila"     would become egg
    'chila'  ~ hilsa   -> "Besan And Moong Dal Chila"     would become nonveg

The 2 genuine hits (`kalia`, `kaila` ~ katla) occur only in `Katla Kalia`,
`Rohu Kalia`, `Katla Kaila` — each of which **already contains an exact protein
token**. So fuzzy protein matching gains nothing and would label sweets and
vegetables as meat.

**Proteins stay exact-match permanently.** Two guards enforce this: tokens
shorter than 6 characters are exact-only (`veg` and `egg` are one edit apart,
per D17), and any token matching the protein or egg lexicon is excluded from
fuzzy qualifier matching entirely. Direction of error also matters — a loose veg
qualifier can only move an item from `unclear` to `veg`, never manufacture a
non-veg claim. Pinned by `test_protein_lexicon_is_never_fuzzy_matched`.

### Cluster consistency (D17 validation)

Against hand labels: **0 clusters contain members with conflicting gold labels.**
A positive result for D17 — but only 7 of 776 clusters had two or more labelled
members, so the check has very little power and rules out little.

Widened to predicted tags across all 776 clusters: 5 conflicts before the egg
fix, **2 after**, and both remaining are genuine upstream variation rather than
bad merges — one hall's `Banana Shake` contains egg and another's does not; one
hall's `Kathi Roll` is marked non-veg and another's is not.

---

## D21. Ground-truth provenance and its limits

**Recorded so that any published tagger number carries its caveats.**

Labels were produced by **a single annotator with direct familiarity with IITK
mess food**. Consequences, all stated rather than implied:

- **No inter-annotator agreement figure exists**, and none can be computed
  retrospectively. Every accuracy number in D20 is measured against one
  person's judgement.
- **Zero items were marked `unclear`.** On review the annotator confirmed every
  item was resolvable — but **partly from domain familiarity rather than from
  string evidence alone**. Only 11% of the sample carried an explicit diet
  marker, so 89% was resolved by inference that a string-only tagger cannot
  reproduce.
- Therefore **tagger errors on such items reflect a genuine limitation of the
  rule-based approach for this deployment, not a labelling defect.** When the
  tagger abstains on `Kathi Roll` and the annotator knows the hall serves the
  vegetarian one, both are behaving correctly given what each can see. The gap
  is real and is a property of the method, not a mistake by either party.

**Consequence for the benchmark.** The ground truth encodes IITK-specific priors
unavailable to any system that reads only the corpus. Results should be read as
"how well can a string-only method reproduce an informed local judgement", not
as "how well does the system know the truth". This is consistent with D16: the
benchmark measures faithfulness to the corpus, and here the ground truth sits
slightly *outside* the corpus.

**Not rejected, but noted for a later phase:** a second annotator on the same
150 items would produce an agreement figure and would show whether the
zero-`unclear` result is a property of the vocabulary or of the annotator.

---

## D22. Chunking: one chunk per (hall, day, meal)

**Decision.** 294 chunks, one per slot. Each carries hall name, hall type,
location, day, meal, the base menu, and the extras, as readable text:

    Hall 12 (Boys hall, Near MT Section) — Wednesday Dinner
    Menu: Kadhai Paneer, Egg Curry, Panchratan Dal
    Extras: White Sauce Pasta, Besan Halwa, Mutton Rogan Josh (Non-Veg)

**Alternatives rejected.**

*Per-item chunks (1,903).* Each item its own vector. Rejected because it
destroys the context that makes a chunk answerable — a vector for
`"Paneer Pasanda"` carries no hall, day or meal, so the citation cannot be
mapped back and D8's lookup and comparison questions become unanswerable without
a join the baseline is not allowed to do. It also multiplies the corpus 6.5x for
a corpus whose whole point is that it is small. **See D25 — Phase 2 produced
evidence this trade-off is worth re-testing as an ablation.**

*Per-hall-day chunks (98).* Breakfast, lunch and dinner in one chunk. Rejected
because every D8 question filters on meal, and merging three meals into one
vector guarantees the wrong-meal retrievals we already see at slot granularity
(Q2 retrieved `hall-4__friday__breakfast` at 0.802, above every correct chunk —
merging would make that worse, not better).

**Fairness constraints on chunk content**, because a rigged strawman would make
the whole comparison worthless:

- Hall metadata is **included**. Without it D8 category 7 is unanswerable by
  construction.
- Item text is `item_raw`, **verbatim** — so upstream's `(Non-Veg)` markers are
  visible to the baseline. Stripping them would cripple it on the questions that
  matter most.
- The Phase 1 **derived diet tags are excluded**. Those are the structured path's
  enrichment; handing a derived column to a "naive" baseline would flatter it
  rather than test it.

---

## D23. Vector storage: a numpy matrix, not FAISS or sqlite-vec

**Decision.** `db/chunks.npy` (294 x 384 float32, 451 KB) plus
`db/chunks.meta.json`, beside `db/khana.db`. Exact flat cosine scan.

**This deviates from the Phase 2 brief**, which said "FAISS or sqlite-vec, not a
server", so the reasoning is recorded rather than assumed.

At n=294 an exact scan is ~0.1 ms and is **strictly more accurate** than any
approximate index — FAISS's value is approximation, which at this scale buys
nothing and costs recall. Vectors are L2-normalised by the model (verified: norms
are exactly 1.0), so cosine similarity is a single `vecs @ q` dot product. The
brief's real constraints — persisted next to the SQLite DB, no server — are both
met, and `requirements.txt` already predicted this in Phase 0 ("a flat cosine
scan over a numpy array is sufficient").

Confirmed by the author: keep numpy. Swapping in FAISS later is ~10 lines.

---

## D24. Embeddings and the LLM provider seam

**Embedding model: BAAI/bge-small-en-v1.5 via `fastembed`.**

`sentence-transformers` is the obvious route and pulls torch (~2 GB). `fastembed`
runs the same model on onnxruntime with no torch: measured total venv **236 MB**.
Same model the brief asked for, at a tenth of the footprint.

bge-v1.5 is trained **asymmetrically** — queries take an instruction prefix,
documents do not:

    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

Applied to queries only. Omitting it measurably degrades retrieval, and a fair
baseline uses the model as its authors intended.

**LLM: Gemini, behind an `LLM` ABC.** `GeminiLLM`, `AnthropicLLM` and `EchoLLM`
all implement one two-method interface, so adding a provider is one class rather
than a refactor. All generation settings (`MAX_TOKENS`, retry policy) live in
`rag/llm.py` and nowhere else, because Phase 3 compares three systems on tokens
and latency and those numbers are only comparable if every system calls the model
identically.

**`gemini-2.5-flash` is dead for new API keys** — it still appears in
`models.list()` but returns `404 ... no longer available to new users. Please
update your code to use models/gemini-3.6-flash`. `gemini-3.7-flash` returned
`503 high demand`. Pinned to **`gemini-3.6-flash`**, the replacement the API
itself names.

**Retry:** 429/503/5xx, 6 attempts, `2^n` seconds with full jitter capped at 64 s,
honouring `retry-after` when present. Needed because free-tier Gemini has
per-minute and per-day caps and Phase 3 will run ~300 queries.

**Thinking tokens are reported separately from completion tokens.** Gemini 3.x
thinks by default; across the Phase 2 smoke test thinking averaged **582
tokens/query against 108 completion tokens — 5.4x the visible answer**. Folding
them into `completion_tokens` would understate cost fivefold. Thinking is left at
the model default rather than disabled, so that Phase 3 can hold the setting
constant across all three systems; the requirement is that the number is
**visible**, not that it is zero.

---

## D25. Per-item vs per-slot chunking is a Phase 5 ablation

**Recorded because Phase 2 produced a mechanism, not just a suspicion.**

`hall-10__tuesday__dinner` serves `Paneer Pasanda` and ranks **47** for "how many
halls serve paneer at dinner on Tuesday" — outside any plausible k. The chunk:

    Menu: Kulcha, Chole, Veg Dal
    Extras: Kerala Style Chicken Curry (Non-Veg), Boiled Chicken (Non-Veg),
            Paneer Pasanda, Kesar Milk

Two chicken dishes dominate the embedding. The same chunk ranks **5th** for the
chicken-negation query. A chunk's position tracks its **dominant term**, not
whether it contains the queried item.

Ranks of all 8 gold chunks for that question, with whether the chunk also carries
non-veg text:

    rank  1  no    hall-6     rank  4  no    hall-13
    rank  2  no    hall-2     rank  5  no    hall-9
    rank  3  YES   hall-14    rank  7  no    hall-5
    rank 19  YES   gh-1       rank 47  YES   hall-10

**Suggestive, not deterministic** — both badly-ranked gold chunks carry competing
non-veg content, but `hall-14` does too and still ranks 3rd. The supportable
claim is weaker: *a chunk holding many competing dishes is at risk of being
pulled away from any one of them*, and per-slot chunking makes that risk
structural by forcing 4–10 dishes into a single vector.

**Ablation:** per-item (1,903 chunks) vs per-slot (294), same model, same k, same
D8 questions. Expected trade — per-item should fix dilution but lose the slot
context that makes lookup and comparison easy. This is why D22's rejection of
per-item chunking is worth re-testing rather than assuming.

---

## D26. Model pinning, and the tier finding

**Decision.** The evaluated model is pinned to the exact string:

    gemini-3.6-flash          version 3.6-flash-07-2026
    input limit  1,048,576 tokens
    output limit    65,536 tokens

Set in `.env` as `GEMINI_MODEL` and defaulted in `rag/llm.py`.

### `gemini-3.6-flash` is a PREVIEW model — three consequences

1. **Billing may be enabled by default.** Preview models frequently are not
   covered by the free tier.
2. **Rate limits are more restrictive** than for GA models. The backoff in D24
   (429/503, six attempts, jittered `2^n` to 64 s) is load-bearing for Phase 3's
   ~300 queries, not decorative.
3. **Google deprecates previews with two weeks' notice.** This model can
   disappear mid-project, exactly as `gemini-2.5-flash` already did (D24: still
   listed by `models.list()`, returns 404 for new keys).

### Tier: the key is NOT on the free tier

**Checked rather than assumed.** A live request returns:

    X-Gemini-Service-Tier: standard

`standard` is Google's label for the paid, pay-as-you-go tier; a free-tier key
reports `free`. **So this key is billing-enabled, and Phase 3's ~300 queries will
incur real charges.**

Flagged because Gemini was chosen partly on the expectation of a free tier with
no per-token cost. The header is the only programmatic signal available — the
API exposes no billing endpoint — so **confirm in Google AI Studio / Cloud
Console before running Phase 3**, and note that Phase 2's smoke test already
consumed billable tokens (7 queries: 2,976 prompt + 758 completion + 4,074
thinking).

**Resolved in D29:** the `standard` header reflects the Cloud project's billing
state, not the author's Google AI Pro subscription — consumer subscription and
developer API are separate products, and API quotas are per-Cloud-project. Cost
is **not a blocker**: ~$4 per full three-system run. No provider change.

Cost is small at this corpus size but must be stated rather than assumed away.
The long-context baseline is the expensive one: the whole corpus measures
**17,147 tokens** (294 chunks, 47,710 chars — D12 estimated ~25k, so the estimate
was high), or **1.6% of the 1M context window**. It fits comfortably, but it is
17k prompt tokens on *every* long-context query versus ~425 for naive RAG.

### Methodology: one model string for the whole table

**All three Phase 3 systems — naive RAG, long-context stuffing, and the
structured router — must be evaluated on the same model string.** Accuracy,
tokens, and latency are only comparable if the generator is held constant; a
table with two models in it compares models, not architectures.

**If the model changes mid-evaluation, the entire results table is rerun, not
patched.** Splicing rows generated by two different models — or two different
preview snapshots of the "same" model — produces a table that looks coherent and
is not. Given consequence 3 above this is a live risk, so the rule is recorded
before it is needed. The pinned version string `3.6-flash-07-2026` is recorded so
a silent snapshot change is detectable.

---

## D27. Temporal resolution is SHARED preprocessing, not a router feature

**Decision.** Resolving "today" / "tonight" / "tomorrow" to a concrete
`(day_of_week, meal)` happens **before** the query reaches any system, and the
result is made available **identically** to naive RAG, long-context stuffing, and
the structured router.

**Why this matters more than it looks.**

Phase 2 measured the temporal question (D8 Q5, *"Which mess has chicken for
dinner tonight?"*) at **0/5 gold chunks retrieved at every k up to 40, and 0/14
slot coverage**. The generator then answered about Friday and Thursday without
ever noting it could not resolve the day — nothing in the prompt carried today's
date, so it had nothing to resolve it with.

If Phase 4 gives the router a temporal resolver and the baseline never gets the
date, **the router wins the temporal category because it was told the day and the
baseline was not.** That measures the experimental setup, not the architecture —
precisely the kind of rigged comparison D16 and the Phase 2 fairness constraints
(D22) exist to prevent. It would be the single easiest finding for a sceptical
reader to dismantle, and they would be right to.

**What the honest test is.** With the day resolved and handed to every system
equally, the temporal category measures whether each system can **use** a
resolved day. That is a real and open question, and retrieval may still lose it:
a query embedding carries only a weak day signal, so even
`"...for dinner on Tuesday"` competes against every other Tuesday-dinner-adjacent
chunk rather than filtering to them. Phase 2 already showed this at slot
granularity — Q2 retrieved `hall-4__friday__breakfast` at 0.802, outranking every
correct chunk. A SQL `WHERE day_of_week = 'Tuesday'` does not have that problem.

So the router may well still win the category. The difference is that it would
then be winning on **architecture** rather than on **information the baseline was
denied**, and that is the only version of the result worth publishing.

**Consequence for Phase 2's numbers.** The baseline as built has **no date
injection**. Its Phase 2 smoke-test results are therefore a pipeline check, not
benchmark inputs. **The Phase 2 baseline must be re-run with date injection
before its Phase 3 numbers are taken.** Q5's current answer in particular is not
a valid baseline datapoint — it is a measurement of a missing prompt field.

**Rejected:** *letting each system resolve dates itself.* It moves a shared
preprocessing step inside the thing being compared, so any difference in
resolver quality contaminates the architecture comparison. Resolution is a solved
problem and not what this project is measuring.

---

## D28. Context caching is the strongest rebuttal to D12, and is met head-on

**The objection, stated at full strength.** D12 added the long-context baseline
because a sceptic will ask why not just stuff all 294 rows into the prompt. The
sharper form of that question is: *the corpus prompt is byte-identical on every
query, so cache it — cached input bills at roughly 10% of base rate, and your
cost advantage evaporates.*

**That objection is largely correct, and the results table must not imply
otherwise.**

The 17,147-token corpus prompt is a near-ideal caching candidate: fixed content,
no per-query variation, reused across the whole benchmark. Gemini supports this
directly — `client.caches` (`create` / `get` / `list` / `update` / `delete`) is
present in the pinned SDK, and cache usage is **measurable at runtime** via
`usage_metadata.cached_content_token_count` and `cache_tokens_details`, so Phase
3 can report observed cache behaviour rather than assert it.

### Phase 3 reports the long-context baseline THREE ways

1. **Uncached token cost** — what it costs with no caching.
2. **Cached-cost equivalent** — the same run priced at the cached input rate.
3. **p50 latency** — wall clock, which caching does not change.

### What caching does and does not close

Per-query input, measured (`gemini-3.6-flash`, $1.50/M input):

    naive RAG            ~425 tokens    $0.00064 / query
    long-context      17,147 tokens     $0.0257  / query   uncached   40x
    long-context, cached input          $0.0026  / query   cached      4x

**Caching collapses most of the cost gap — 40x down to 4x — but not all of it.**
Stating "4x" rather than "parity" matters: the remaining gap is real but small
enough that cost alone is not a winning argument.

**Caching does nothing for latency.** The 17,147 tokens are still *processed* on
every query; caching avoids re-billing them, not re-reading them. **And it does
nothing for accuracy** — a cached wrong answer is still wrong.

### The defensible claim, narrowed

D12 as originally written implied a cost advantage that caching largely erases.
The honest, narrower claim:

> **Caching closes the cost gap. The accuracy and latency gaps are
> architectural.**

The results table must be read that way, and the write-up must say it in those
words rather than letting a cost column imply an advantage a sceptic can delete
with one API call. If the router's case rests on cost, it does not have a case;
its case has to rest on accuracy and latency.

**Two confounds Phase 3 must control for**, both to be verified at the time
rather than assumed now:

- **Implicit caching.** Gemini may cache repeated prefixes automatically for some
  models. If so, the "uncached" column is not actually uncached and must be
  labelled from the observed `cached_content_token_count`, not from intent.
- **Explicit-cache overheads.** Explicit caches carry a storage cost per
  token-hour and a minimum cacheable size. Both need checking against current
  pricing before the cached-cost column is quoted, since at 17k tokens the
  minimum-size threshold is the one most likely to bite.

---

## D29. Two-model strategy: cheap for iteration, pinned for the table

**Decision.**

| Use | Model |
|---|---|
| Development, dry runs, harness debugging, question authoring | a **free-tier Flash-Lite** model (candidates present in `models.list()`: `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-flash-lite-latest`) |
| **The final Phase 3 results table** | the pinned **`gemini-3.6-flash`** / `3.6-flash-07-2026`, all three systems, one run |

Iteration should not be paid for. The table should not be cheap.

**Why the final table is NOT run on Flash-Lite.**

Phase 2's headline finding — the generator hallucinated nothing across seven
questions, produced zero invented citations, and twice rejected retrieved chunks
as off-slot unprompted — is **a property of the model, not of RAG.** That
distinction is load-bearing for the whole project:

- The Phase 2 conclusion was *"the baseline's failures are retrieval failures,
  not generation failures,"* which is what makes it a fair strawman.
- **On a weaker model, naive RAG may fail for generation reasons instead** —
  fabricating halls it never retrieved, or citing excerpts that do not exist.
- The headline result would then be partly an artifact of model choice, and a
  reader could not tell which failures were architectural and which were the
  generator being weak.

Long-context needle-finding is likewise model-dependent — a weaker model may lose
facts inside a 17k-token prompt that a stronger one retains. That risk is
**smaller** here, since 17,147 tokens is only 1.6% of the 1M window and well
clear of the region where long-context degradation usually appears, but it is not
zero.

**If quota or cost later forces the final table onto Flash-Lite**, the Phase 2
smoke test is **re-run on that model first** to check whether the
no-hallucination property still holds. **Do not inherit the finding silently.**
If it does not hold, that is itself a reportable result — it would mean the
fairness of the baseline depends on generator strength, which is worth a row in
the write-up rather than a silent footnote.

This sits alongside D26's rule that the whole table is rerun rather than patched
if the model changes mid-evaluation. The two together: **one model for the table,
and any change to it invalidates the table rather than a row of it.**

### Tier question from D26, resolved

The `X-Gemini-Service-Tier: standard` header is explained: **a Google AI Pro
consumer subscription does not affect developer API quotas.** The consumer
subscription and the developer API are separate products, and API limits are
per-Cloud-project. So the header reflects the Cloud project's billing state, not
the subscription.

**Cost is not a blocker** — approximately **$4 per full three-system run** at
$1.50/$7.50 per million, less if a discounted rate applies. No provider change.
Recorded so the number is on the record rather than rediscovered later; the
dominant term is the long-context arm's 17,147 prompt tokens per query (D28).
