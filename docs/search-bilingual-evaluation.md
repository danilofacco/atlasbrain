# Bilingual search evaluation — 2026-09-30

The new corpus contains **100 queries: 50 Portuguese and 50 English**, describing
50 paired intents across AtlasBrain, Olax, SabeTudo, Sushigame and Adsivos. This is
40 positive intents (80 queries) and 10 deliberately empty filter scopes
(20 queries), rather than 100 independent questions.

Questions and expected files were authored after reading source, before retrieving
results. They cover URL imports, edit conflicts, rename links, consolidation,
update recovery, task budgets, AI action limits, asynchronous state changes,
message redaction, publication queues, mobile lifecycle, authentication and
accessibility. Three projects also have a reviewed decision/learning intent.
These are fresh behavioral questions, rather than adding answer-derived filters
to the previous 97-question set.

## Evaluation protocol

- PT/EN versions share the same intent, evidence, expected files and split. Labelled
  files also stay in one split. The calibration split has 32 positive queries and
  10 negatives; validation has 48 positive queries and 10 negatives.
- SHA-256 hashes, reviewed source line ranges and the indexed SHA-1 are checked.
  Changed sources or stale indexes stop evaluation and require label review.
- Baseline `8cccda5` and the current filter implementation run on identical
  SQLite backup snapshots taken through read-only connections. Ranking weights were not tuned on these cases.
- Three interleaved repetitions check deterministic result order. Model and search
  caches are warmed outside timing. The embedding model must already be cached;
  no project contents are uploaded and no model is downloaded by the evaluator.
- Top-1, Top-3, Recall@10 and MRR@10 use positive queries only. A hit means **any
  reviewed expected file** is returned; this does not measure passage completeness,
  generated answer correctness or multi-file evidence coverage.
- Negative cases have folders or tags verified absent from each frozen index.
  They measure strict filter behavior. They do **not** test abstention on arbitrary
  unanswerable natural-language questions, and never increase positive ranking scores.
- Reported latency includes positive and negative queries. It is warmed timing from
  one local run, including query embedding, rather than a universal speed estimate.
- Public results contain only aggregate scores. Full private queries, evidence,
  labels and diagnostic ranks remain under the ignored local brain directory.

## Results

| Language | Positive queries | Top-1 before → current | Top-3 before → current | Recall@10 before → current |
|---|---:|---:|---:|---:|
| Portuguese | 40 | 52.5% → 52.5% | 70.0% → 70.0% | 87.5% → 90.0% |
| English | 40 | 60.0% → 62.5% | 77.5% → 80.0% | 85.0% → 87.5% |

| Project | Positive queries | Current Top-1 | Current Top-3 |
|---|---:|---:|---:|
| SabeTudo | 16 | 31.2% | 75.0% |
| adsivos | 16 | 56.2% | 68.8% |
| atlasbrain | 16 | 87.5% | 87.5% |
| olax | 16 | 50.0% | 68.8% |
| sushigame | 16 | 62.5% | 75.0% |

Overall positive Top-3 moves from **73.75% to 75%** and Recall@10 from **86.25%
to 88.75%**. The filter cases account for the gains; unrestricted code-query
Top-3 remains **70.31%**. This run does not demonstrate a broad ranking improvement.
Both versions correctly return no results for all 20 negative scope queries.
Neither version leaks results outside the measured metadata filters.

All six memory queries rank first in both versions. They represent only three
independent memory topics; more lifecycle and contradictory-decision cases are
needed before treating that score as general memory retrieval quality.

For the current engine, 26 of 40 positive intents pass Top-3 in both languages,
2 pass only in Portuguese, 6 only in English and 6 in neither. The Portuguese
weakness deserves investigation, rather than assuming that English always performs
better from this small source-selected sample. Translations are correlated cases.

## Where to inspect and reproduce

The complete local corpus is `.atlasbrain/benchmarks/bilingual-2026-09-30.jsonl`.
The original benchmark files remain intact. The public subset contains 18 queries
about AtlasBrain source and empty scopes in
[`bilingual_public.jsonl`](../benchmarks/bilingual_public.jsonl); private memory
questions and the other projects' questions are excluded.

Run the five-project evaluation using the complete corpus and an explicit
`--vault name=/absolute/path` for each project. The evaluator checks that mappings
match the dataset; it never guesses a project from a global service default.

```sh
uv run python -m benchmarks.evaluate_bilingual \
  --cases .atlasbrain/benchmarks/bilingual-2026-09-30.jsonl \
  --vault atlasbrain=/absolute/path/to/atlasbrain \
  --vault olax=/absolute/path/to/olax \
  --vault SabeTudo=/absolute/path/to/SabeTudo \
  --vault sushigame=/absolute/path/to/sushigame \
  --vault adsivos=/absolute/path/to/adsivos \
  --before-ref 8cccda5 --repeats 3 \
  --output /tmp/search-bilingual.json \
  --private-output .atlasbrain/benchmarks/bilingual-results.json
```

The measured aggregate report, including revisions, source/dataset hashes,
per-language/per-project/per-split slices and timings, is
[`search-bilingual-2026-09-30.json`](../benchmarks/results/search-bilingual-2026-09-30.json).
These questions have now been evaluated. They remain useful regression tests,
but once used to tune ranking, a further untouched set is required for independent
validation. Source-selected questions are not a production query distribution.
