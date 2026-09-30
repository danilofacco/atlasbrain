# Search performance — 2026-09-30

A fresh run used the same 100 bilingual queries (50 paired intents) and five
interleaved repetitions per query/version. Baseline `8cccda5` and the current
filter engine ran on identical SQLite snapshots. The query embedding model was
already cached and warmed. Search source and ranking parameters are unchanged
since the bilingual evaluation. Timings cover the engine, excluding first
startup, client rendering and MCP transport.

The 80 positive queries are reported separately from 20 empty filter scopes.
This prevents cheap empty-scope returns from making ordinary search look faster.

| Positive-query metric | Before | Current |
|---|---:|---:|
| Median latency | 43.36 ms | 37.72 ms |
| P95 latency | 172.68 ms | 171.34 ms |
| Top-1 | 56.25% | 57.50% |
| Top-3 | 73.75% | 76.25% |
| Recall@10 | 86.25% | 88.75% |

The median decreases by **13.0%** in this run. Global P95 is nearly
unchanged. These local timings are samples on one machine, not a guaranteed
production speedup.

| Project | Indexed files | Positive queries | Median before | Median current | P95 before | P95 current | Current Top-3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SabeTudo | 177 | 16 | 44.0 ms | 38.6 ms | 172.6 ms | 105.2 ms | 75.00% |
| adsivos | 215 | 16 | 33.9 ms | 29.9 ms | 49.4 ms | 44.3 ms | 68.75% |
| atlasbrain | 139 | 16 | 48.9 ms | 44.1 ms | 111.7 ms | 95.9 ms | 93.75% |
| olax | 3265 | 16 | 118.2 ms | 110.1 ms | 219.1 ms | 236.3 ms | 68.75% |
| sushigame | 174 | 16 | 39.2 ms | 34.2 ms | 48.4 ms | 44.8 ms | 75.00% |

All 20 empty folder/tag scope queries return no results in both versions, with
zero metadata scope violations. Their median drops from 18.99 ms
to 0.18 ms. This is the clearest filter-specific speed improvement;
it does not measure natural-language abstention.

## Where the remaining time goes

A separate profiling pass measured all 80 positive questions, twice each, in the
current engine. It times query encoding inside `embed.embed`; remaining time
includes lexical/structural retrieval, vector scoring, passage aggregation and
result ordering. The shares below use summed durations, rather than dividing
separate medians. They are diagnostic measurements from a separate run.

| Project | Query encoding share | Remaining engine share |
|---|---:|---:|
| atlasbrain | 46.9% | 53.1% |
| olax | 18.5% | 81.5% |
| SabeTudo | 48.5% | 51.5% |
| sushigame | 51.1% | 48.9% |
| adsivos | 51.5% | 48.5% |

Olax remains the largest indexed project in this corpus. Its median improves,
but its P95 increases: reducing the slow tail needs investigation. About 81.5%
of its profiled time is outside query encoding. Profile lexical candidate
retrieval, structural scans and result aggregation next; the aggregate phase
measurement cannot yet identify which individual operation dominates. Other
projects spend roughly half their measured time encoding the query, making a
bounded query-vector cache another candidate to test. Such a cache would avoid
re-encoding repeated questions, but would not speed up every new question.

The retrieval quality also still needs work: returning an answer quickly is
separate from ranking the best file first. SabeTudo Top-1 is 31.25%; Olax and
Adsivos Top-3 are 68.75% on these small labelled samples.

## Interpretation and reproducibility

AtlasBrain and Sushigame index revisions changed since the previous three-repeat
run. One Portuguese AtlasBrain expected file moves from rank 4 to rank 3 in the
current engine. The fresh overall Top-3 is 76.25%, while the previous run was
75%; this difference is index/corpus evolution, not newly tuned ranking weights.
Compare engine versions **within this run**, where both use the same snapshots.

Timing also varies substantially across local runs. The prior positive-query
current median was 152.41 ms; the fresh one is 37.72 ms with the same search
source. That cross-run difference cannot be attributed to a code optimization.
The before/after comparison above uses interleaved runs and fixed snapshots to
reduce that source of confusion.

Use `benchmarks.evaluate_bilingual` with the frozen local corpus, all five
explicit `--vault` mappings, `--before-ref 8cccda5` and `--repeats 5`, as documented
in [the bilingual evaluation](search-bilingual-evaluation.md). Positive-only
cohorts here are recomputed from the stored per-query timing samples. The original
three-repeat report is retained separately. Private questions, labels and returned
file paths stay inside the ignored local brain.

Aggregate metrics, source/dataset hashes, index revisions and the separate phase
profile are in [the performance report](../benchmarks/results/search-performance-2026-09-30.json).
