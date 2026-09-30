# Search filters: correctness and before/after evaluation

Measured on September 30, 2026 against the original 0.2.1 search implementation
at commit `8cccda5`. The comparison uses identical frozen SQLite copies and five
interleaved repetitions per query. Embeddings use the already-cached local
multilingual MiniLM model. No ranking weights, classifier or index schema changed.

## What changed

Folder, tag, type, status, date, phrase and exclusion constraints now determine
eligible passages before each candidate source's limit. The lexical and semantic
sources keep their global ranks for reciprocal-rank fusion while filling the
scoped candidate pool. Re-ranking source positions inside the scope was rejected
because it reduced Top1/Top3 in development comparisons.

Eligibility is computed once per query. Note metadata is checked before iterating
its chunks. FTS5 scans the matching text index once, then produces snippets only
for selected eligible passages. This avoids a rowid query plan that restarts MATCH
for every eligible row. Exact file/symbol lookup selects an eligible passage too.

Queries containing only filters now list matching files, newest first, with one
passage per file. `pasta:/` means the whole project; folder boundaries and literal
`%`/`_` characters are respected. Unicode casing works for accented folder names
and phrases. Type aliases include `decisão`, `decisões`, `decisoes` and `código`.
Quoted phrases and exclusions retain their existing title-plus-passage semantics;
they are not new whole-file contradiction or cross-chunk phrase detectors.

The correction applies to the search engine shared by MCP, CLI and the browser.
No extra persistent service, port, external API or model is introduced.

## Correctness fixtures

An isolated corpus places 205 stronger but ineligible documents ahead of the
eligible answer. Ten cases cover folder, tag, type, status, date, exclusion,
quoted phrase, combined constraints, filters-only listing and a plural type alias.
The expected file is known from fixture construction.

| Measure | Before | After |
| --- | ---: | ---: |
| Correct first result | 0/10 | 10/10 |
| Incorrect empty responses in these cases | 10/10 | 0/10 |

Nonexistent folder queries stayed empty in both versions. Separate regression
checks exercise lexical, semantic and symbol sources independently, exact-match
passage selection, Unicode, folder boundaries, literal wildcards and filter-only
pagination limits. Fixture success is not an estimate of general search accuracy.

## Existing project questions

The current local reviewed sets contain 97 questions: AtlasBrain 17, Olax 32,
SabeTudo 8, Sushigame 20 and Adsivos 20. SabeTudo's cases come from its saved local
benchmark report; the other projects use `benchmark.jsonl`. AtlasBrain's current
17-case set differs from the older 24-case code pilot, so historical percentages
from that pilot are not directly comparable here.

Across the 97 original queries, every expected-file rank stayed identical and
Top1/Top3 were preserved. The following folder queries are **derivatives** of those
questions, using the reviewed answer's parent folder as an explicit filter.
They test scope handling and do not constitute independent natural-language labels.

| Project | Cases | Top1 before → after | Top3 before → after | Warm p50 before → after |
| --- | ---: | ---: | ---: | ---: |
| AtlasBrain | 17 | 70.6% → 70.6% | 88.2% → 88.2% | 5.4 → 4.9 ms |
| Olax | 32 | 65.6% → 65.6% | 87.5% → 87.5% | 23.3 → 16.8 ms |
| SabeTudo | 8 | 37.5% → 50.0% | 75.0% → 75.0% | 10.2 → 5.4 ms |
| Sushigame | 20 | 80.0% → 80.0% | 95.0% → 95.0% | 16.0 → 10.1 ms |
| Adsivos | 20 | 60.0% → 60.0% | 95.0% → 95.0% | 29.3 → 19.7 ms |

Type-scoped derivatives also preserved Top1 and Top3 in every project. Broad
`tipo:codigo` searches in Olax were slower: p50 25.1 → 27.9 ms and p95
33.8 → 44.0 ms. This scope includes much of its index and adds eligibility work
without removing many candidates. Its folder-scoped p95 improved from 30.3 to
24.2 ms. Adsivos type-scoped MRR moved from 0.539 to 0.533 despite unchanged
Top1/Top3; two lower result positions changed. No universal accuracy or latency
improvement is claimed.

All five snapshot revisions stayed stable. There were 291 project queries across
original, folder and type cohorts, plus ten fixture cases, with five measured
repetitions for each version. Times exclude model initialization and cold vector
cache loading. Background system activity, small corpora and repeated development
labels limit conclusions; the eight SabeTudo cases are especially provisional.
These measurements do not evaluate false positives, passage-level accuracy or
context token costs. A new held-out corpus is needed before expanding reranking.

The [aggregate result](../benchmarks/results/search-filters-2026-09-30.json)
contains per-cohort Top1/Top3/MRR, p50/p95 and anonymous changed case positions.
Private questions and expected paths are not included.

## Reproduce

```sh
uv run python -m benchmarks.evaluate_filters \
  /absolute/path/to/project \
  --before-ref 8cccda5 --repeats 5 --output /tmp/filter-comparison.json
```

Omit the project argument to run fixtures alone. The harness requires the model
cache for semantic project runs and reads SQLite snapshots without changing the
live indexes. `ATLASBRAIN_NO_EMBED=1` runs a different, lexical-only comparison.
