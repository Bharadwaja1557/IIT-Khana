# IIT Khana — Phase 3 results

Model: **`gemini-3.5-flash-lite`** (all systems, one string — D26/D35).  
Questions scored: **59** per system. Errors: **0**. Unscored (unlabelled): **1**.

No interpretation in the tables below. See *My read* at the end.

## Accuracy

| category | n | naive RAG exact | naive RAG F1 | long-context exact | long-context F1 |
|---|---:|---:|---:|---:|---:|
| lookup | 10 | 90% | 0.99 | 70% | 0.92 |
| comparison | 10 | 70% | 0.77 | 90% | 0.90 |
| aggregation | 10 | 50% | 0.90 | 20% | 0.59 |
| negation | 10 | 0% | 0.13 | 20% | 0.85 |
| temporal | 10 | 30% | 0.69 | 0% | 0.62 |
| fuzzy_semantic | 9 | 0% | 0.29 | 0% | 0.47 |
| **OVERALL** | **59** | **41%** | **0.63** | **34%** | **0.73** |

## Trivial baselines (no LLM, no retrieval)

| baseline | category | exact | F1 |
|---|---|---:|---:|
| name-all-14 | comparison | 0% | 0.13 |
| name-all-14 | aggregation | 0% | 0.34 |
| name-all-14 | negation | 0% | 0.79 |
| name-all-14 | temporal | 0% | 0.40 |
| name-none | comparison | 0% | 0.00 |
| name-none | aggregation | 0% | 0.00 |
| name-none | negation | 0% | 0.00 |
| name-none | temporal | 0% | 0.00 |
| random-2way | comparison | 50% | 0.50 |

## Tokens per query

| system | category | prompt | cached | completion | thinking |
|---|---|---:|---:|---:|---:|
| naive_rag | lookup | 539 | 0 | 97 | 0 |
| naive_rag | comparison | 582 | 0 | 70 | 0 |
| naive_rag | aggregation | 611 | 0 | 93 | 0 |
| naive_rag | negation | 572 | 0 | 127 | 0 |
| naive_rag | temporal | 581 | 0 | 79 | 0 |
| naive_rag | fuzzy_semantic | 565 | 0 | 89 | 0 |
| long_context | lookup | 17432 | 11036 | 75 | 0 |
| long_context | comparison | 17438 | 12262 | 76 | 0 |
| long_context | aggregation | 17431 | 12262 | 91 | 0 |
| long_context | negation | 17432 | 12262 | 378 | 0 |
| long_context | temporal | 17444 | 7357 | 149 | 0 |
| long_context | fuzzy_semantic | 17433 | 12262 | 121 | 0 |

## p50 latency (ms)

| system | category | p50 total | p50 generation | p50 retrieval |
|---|---|---:|---:|---:|
| naive_rag | lookup | 1013 | 945 | 97 |
| naive_rag | comparison | 1007 | 939 | 75 |
| naive_rag | aggregation | 1110 | 1041 | 65 |
| naive_rag | negation | 1114 | 1040 | 67 |
| naive_rag | temporal | 997 | 907 | 82 |
| naive_rag | fuzzy_semantic | 1000 | 915 | 85 |
| long_context | lookup | 1157 | 1156 | 0 |
| long_context | comparison | 1291 | 1290 | 0 |
| long_context | aggregation | 1170 | 1169 | 0 |
| long_context | negation | 1848 | 1846 | 0 |
| long_context | temporal | 1264 | 1262 | 0 |
| long_context | fuzzy_semantic | 1316 | 1315 | 0 |

## Precision when answering (non-abstained questions only)

Additional column, **not** a replacement: the headline Accuracy table above scores abstentions as incorrect. The two systems have very different answer policies, so without this the comparison is partly measuring willingness to answer rather than architecture.

| system | category | all (headline) | when answering | n answered | abstained |
|---|---|---:|---:|---:|---:|
| naive_rag | lookup | 90% | 90% | 10 | 0 |
| naive_rag | comparison | 70% | 80% | 5 | 5 |
| naive_rag | aggregation | 50% | 56% | 9 | 1 |
| naive_rag | negation | 0% | 0% | 6 | 4 |
| naive_rag | temporal | 30% | 50% | 6 | 4 |
| naive_rag | fuzzy_semantic | 0% | 0% | 2 | 7 |
| long_context | lookup | 70% | 70% | 10 | 0 |
| long_context | comparison | 90% | 90% | 10 | 0 |
| long_context | aggregation | 20% | 20% | 10 | 0 |
| long_context | negation | 20% | 22% | 9 | 1 |
| long_context | temporal | 0% | 0% | 10 | 0 |
| long_context | fuzzy_semantic | 0% | 0% | 8 | 1 |

## Abstention rate (scored as incorrect, reported separately)

| system | category | abstained | no ANSWER line |
|---|---|---:|---:|
| naive_rag | lookup | 0/10 | 0/10 |
| naive_rag | comparison | 5/10 | 0/10 |
| naive_rag | aggregation | 1/10 | 0/10 |
| naive_rag | negation | 4/10 | 0/10 |
| naive_rag | temporal | 4/10 | 0/10 |
| naive_rag | fuzzy_semantic | 7/9 | 0/9 |
| long_context | lookup | 0/10 | 0/10 |
| long_context | comparison | 0/10 | 0/10 |
| long_context | aggregation | 0/10 | 0/10 |
| long_context | negation | 1/10 | 0/10 |
| long_context | temporal | 0/10 | 0/10 |
| long_context | fuzzy_semantic | 1/9 | 0/9 |

## Footnotes

**Gate variant per category** (D15 and its substitutes):

- `lookup` — substitute-for-D15 — slot must hold >= 3 items (the hall is named in the question, so there is no hall set to be vacuous about)
- `comparison` — substitute-for-D15 — the two named halls must DIFFER on the predicate, so exactly one is correct
- `aggregation` — D15 as written — gold names 2..12 of 14 halls
- `negation` — D15 as written + D14 — gold names 2..12 of 14 halls AND the predicate must be a meaningful-absence (group-2) item
- `temporal` — D15 as written — gold names 2..12 of 14 halls
- `fuzzy_semantic` — D15 applied to the hand label — gold names 2..12 of 14 halls

**Latency** — measured in a single sitting that did not approach the quota cap (D35). Calls exceeding **60 s** generation time are flagged as suspected provider-side throttling: **0 of 120 calls**. Flagged calls are **included** in p50, not dropped.

**Cached tokens** — read from `usage_metadata.cached_content_token_count` per call. Gemini caches repeated prefixes **implicitly**, with no cache object and no opt-out (D33), so no column here can be labelled "uncached"; the figures are what was observed.

**Abstentions** are scored **incorrect**, never excluded (same accounting as the Phase 1 tagger).

**Unscored** — 1 question(s) carry no hand label and are excluded from every figure: `test-fuzzy_semantic-006`.


---

## Side experiment (NOT a table row) — reasoning model on aggregation

**Status: BLOCKED, no result.** Long-context over the 10 aggregation questions on
`gemini-3.6-flash` (which emits thinking tokens) was attempted to test whether
long-context's aggregation failure is a property of the architecture or of a
non-reasoning model. **6 questions attempted, 6x `429 RESOURCE_EXHAUSTED`, zero
successful calls** — the 20/day free-tier allowance for that model (D34) was
already spent in the same quota period.

Nothing is reported from it. Zero successful calls is not a weak signal, it is
no signal. The experiment is ready to run unchanged and resumable
(`eval/side_thinking.py`); it needs 10 calls and touches nothing above.

---

## My read

*This section is interpretation, not measurement. The tables above are the
result; this is my reading and should be argued with.*

**1. Neither baseline is good — 41% vs 34% exact match.** Both are far from
usable. That is the point: these are the systems the router must beat, and the
table establishes the floor honestly rather than flattering either.

**2. Long-context does NOT dominate naive RAG.** Stuffing all 294 slots costs
**40x the prompt tokens** and wins only 2 of 6 categories on exact match. It
loses lookup (70% vs 90%), aggregation (20% vs 50%) and temporal (0% vs 30%).

**Stated narrowly, because the obvious objection is UNTESTED.** This model emits
**zero thinking tokens**; Phase 2 measured 582/query on `gemini-3.6-flash`. And
long-context lost precisely the categories needing multi-step work. So the
finding may be *"a non-reasoning model cannot aggregate 294 rows in one forward
pass"* rather than *"having every fact in the prompt is not the same as being
able to use it"*. The side experiment to settle it was blocked by quota. **Until
it runs, item 2 holds for a non-reasoning model and its generality is unknown.**

**3. Negation is where the trivial-baseline row earns its keep.** Long-context's
0.85 F1 looks strong until the row above shows `name-all-14` scoring **0.79** on
the same questions. Almost all of it is available by naming every hall and
thinking about nothing. Exact match tells the honest story: **20%**. Naive RAG's
0% exact / 0.13 F1 is Phase 2's mechanism at scale — retrieval returns the halls
that DO serve the item, the precise opposite of the answer. **CHECK 2 confirms
this is not an abstention artefact:** excluding its 4 abstentions leaves naive
RAG at 0% — the 6 answers it committed to were all wrong.

**4. Aggregation splits the systems as Phase 2 predicted** — naive RAG 50%,
long-context 20%. Counting needs every hall in the slot; top-k cannot see them.
Yet long-context, which *can* see all 14, does worse: it has the information and
mis-aggregates it. **But see item 8b — this is bounded by gold size.**

**5. Temporal: long-context scores 0% exact.** Both systems got identical
resolved dates (D27), so this is not Phase 2's withheld-information artefact.
Handed the day, long-context still failed every temporal question.

**6. Fuzzy semantic — 0% exact for both.** *(Revised after CHECK 2; the first
version of this item overclaimed.)* I originally wrote "a keyword list beats both
LLM baselines". That is substantially **"a keyword list beats a system that
declined to answer 7 of 9 times"** — naive RAG abstained on 7 of 9 fuzzy
questions, so its 0% says little about semantic ability. The claim that survives
applies to **long-context**, which answered **8 of 9** and still scored **0%
exact / F1 0.47**, against a keyword list's **F1 0.954** on the same labels. That
still reframes D10 — the retrieval path must beat a keyword list without
per-concept hand authoring — but naive RAG's fuzzy number is not evidence for it.

**7. Implicit caching is enormous and confirms D33.** Prompts of ~17,430 tokens
returned **7,357–12,262 cached** — up to 70% — with no cache object and no
opt-out. Any cost claim about this workload that ignores provider-side caching is
wrong by roughly threefold.

**8. Latency inverts the Phase 2 expectation.** Long-context p50 is 1.16–1.85 s
against naive RAG's 1.00–1.11 s. The 40x token difference costs almost nothing in
wall clock, and **zero calls were flagged as throttled**. The 272-second call in
D34 was a quota-boundary artefact of the previous model, not a property of
long-context.

**8b. Aggregation golds are small, which bounds item 4 (CHECK 3).** Gold sizes
are `[2,2,2,2,3,3,3,3,4,5]` — nine of ten name 4 halls or fewer. That resolves an
apparent contradiction with Phase 2's "k=76 needed for full slot coverage": naive
RAG does not need all 14 halls when the answer is 2–3 of them and they rank near
the top. `corr(gold_n, exact) = -0.53` for naive RAG, and it went 0/1 on the one
question with gold >= 5. **So the aggregation result is partly a property of the
D15 band, not purely of the architecture**, and must not be extrapolated to
larger gold sets — one data point above gold=4 is far too thin to locate the
collapse. Negation shows no such effect: naive RAG is at 0% exact at every size.

**9. What I would not conclude from this table.** These are accuracies on
*discriminating* questions (D31 — 68% of candidates were rejected as vacuous), so
they are not field accuracy. Comparison carries a 50% random-guess floor.
Temporal is aggregation plus a relative day (D32), so those rows are not
independent evidence. A lookup grader defect was corrected after scores were seen
(disclosed above, both numbers). Aggregation golds skew small (8b). And the
reasoning-model objection to item 2 is untested.
