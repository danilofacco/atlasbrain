# Engine reliability and evaluation

These engine features use the shared local daemon and global MCP endpoint with per-request project routing.

## Search evaluation

`evaluate_search(questions, tokens=2000)` accepts 1–100 labelled cases: `{"query":"...","expected_paths":["path/to/file.py"]}`. Without arguments it reads `.atlasbrain/benchmark.jsonl`. Expected files must already be indexed; labels are never generated from search results. Reports include Top1/3/5, MRR, warmed search latency, context latency, context characters, estimated tokens and index stability. Results stay in `.atlasbrain/.editor-backups/benchmark-latest.json`. Token counts use the existing character budget, not a model tokenizer.

Initial measurement on September 29, 2026:

| Project | Cases | Top1 | Top3 | MRR | Search p50 / p95 |
|---|---:|---:|---:|---:|---:|
| AtlasBrain | 24 | 33.3% | 45.8% | 0.444 | 8.5 / 11.3 ms |
| Olax | 32 | 46.9% | 75.0% | 0.598 | 30.1 / 37.9 ms |
| SabeTudo | 8 | 37.5% | 37.5% | 0.405 | 11.7 / 15.4 ms |

AtlasBrain uses `benchmarks/atlasbrain_code_queries.json`; Olax uses its existing labelled corpus. SabeTudo is a small provisional set derived from reading five source files, concentrated on advertising and catalog synchronization. These are development baselines, not independent general-quality estimates or proof of an accuracy improvement. All three indexed revisions stayed stable during their runs; context output stayed within 2,000 estimated tokens. Private projects' complete cases/results remain local.

### Ranking adjustment on September 29, 2026

Failure review showed that implementation questions frequently returned matching decisions, summaries or README files above the implementation. We tested the search signals and candidate grouping on the same labelled sets. Grouping all chunk signals by file reduced Olax accuracy, so the final change keeps the existing chunk fusion. For questions about locating or explaining implementation, indexed code receives a 1.8 score multiplier and the semantic signal weight rises from 1.0 to 1.25. Queries in a notes-only vault, explicit `type:` searches and questions about recorded decisions keep the original semantic weight; exact matches and filters retain their priority. The structural signal and candidate pool remain unchanged. Results include `query sobre implementação` as an explanation when that multiplier applies.

| Project | Cases | Top1 before → after | Top3 before → after | MRR before → after | Recall@50 after |
|---|---:|---:|---:|---:|---:|
| AtlasBrain | 24 | 33.3% → 58.3% | 45.8% → 66.7% | 0.444 → 0.682 | 100% |
| Olax | 32 | 46.9% → 56.3% | 75.0% → 75.0% | 0.598 → 0.655 | 93.8% |
| SabeTudo | 8 | 37.5% → 37.5% | 37.5% → 37.5% | 0.405 → 0.430 | 100% |

At that stage, Top3 failures were 8/24 AtlasBrain cases, 8/32 Olax cases and 5/8 SabeTudo cases. Only two Olax expected files were absent from the top 50; the other failures were ranking problems. The `evaluate_search` report includes `rank_50` and a per-case diagnosis. A separate 20-question notes-only regression fixture remained at 95% Top1 and 0.975 MRR. This was a comparison on development corpora used during tuning, not held-out evidence of general improvement. Search p50 after the change was 8.1 ms for AtlasBrain, 28.9 ms for Olax and 12.2 ms for SabeTudo; the eight SabeTudo timing samples are too few to draw a reliable latency conclusion. The code-embedding policy was measured separately in the next calibration.

### Five-project calibration on September 29, 2026

Sushigame and Adsivos now each have 20 source-reviewed, labelled questions in their own `.atlasbrain/benchmark.jsonl`. Each corpus alternates 10 calibration and 10 validation cases. The expected paths were checked against the source and index before running search. All 40 expected files were in the top 50 with the prior index, so the main problem was ordering. The SabeTudo set remains small and provisional; the earlier AtlasBrain and Olax sets are development baselines.

We compared summary-only code embeddings with full code-chunk embeddings on isolated SQLite copies before changing live indexes. Full embeddings improved the calibration split for Sushigame from 1/10 to 3/10 Top1 and 4/10 to 7/10 Top3; Adsivos stayed at 5/10 Top1 and improved from 6/10 to 7/10 Top3. The existing rank weights were left unchanged. The default `ATLASBRAIN_EMBED_CODIGO=auto` now embeds code bodies when a project has at most 1,000 code-body chunks. Larger projects keep summary-only code embeddings to bound cost and avoid flooding the semantic candidate pool. `ATLASBRAIN_EMBED_CODIGO=summary` or `all` explicitly overrides the automatic policy. This is a per-index policy, not a per-query classifier.

| Project | Cases | Top1 before → after | Top3 before → after | MRR before → after | Search p50 before → after |
|---|---:|---:|---:|---:|---:|
| AtlasBrain | 24 | 58.3% → 62.5% | 66.7% → 83.3% | 0.682 → 0.754 | 7.8 → 8.0 ms |
| Olax | 32 | 56.3% → 56.3% | 75.0% → 75.0% | 0.655 → 0.655 | 28.8 → 24.0 ms |
| SabeTudo | 8 | 37.5% → 37.5% | 37.5% → 37.5% | 0.430 → 0.430 | 12.8 → 9.6 ms |
| Sushigame | 20 | 20.0% → 50.0% | 45.0% → 85.0% | 0.359 → 0.674 | 10.1 → 9.9 ms |
| Adsivos | 20 | 50.0% → 50.0% | 55.0% → 60.0% | 0.536 → 0.568 | 11.1 → 9.5 ms |

The reserved validation cases changed from 3/10 to 7/10 Top1 and 5/10 to 10/10 Top3 in Sushigame. Adsivos stayed at 5/10 Top1 and 5/10 Top3; its remaining misses are ordering failures, often where generic catalog or membership vocabulary raises nearby files. We should improve candidate discrimination there with more reviewed questions before changing global ranking weights. All five benchmark revisions stayed stable and both new projects retained 100% Recall@50. Latencies are local warmed runs, not a deployment guarantee; Olax and SabeTudo did not receive the new body embeddings. One-time body-vector generation took about 3.4 s for 203 AtlasBrain chunks, 9.4 s for 641 Sushigame chunks and 6.0 s for 439 Adsivos chunks on this machine. Subsequent unchanged index passes do not recompute them. A later AtlasBrain run after recording this policy as a project decision reached 66.7% Top1 and 87.5% Top3; that run had a changed local index and is not part of the controlled before/after comparison.

### Two-stage ranking and retired classifier

The optional Laya classifier was removed after its relevance reordering reduced Top1 on the 24-question code pilot from 54.2% to 29.2%, while adding about 1.6 seconds at p50. The current second stage reuses the query embedding already computed by hybrid retrieval, takes the strongest cosine similarity among each of the first three candidate files' chunks, and reorders only those three. It runs for implementation questions when all code-body chunks in a project of at most 1,000 such chunks have embeddings. Exact matches keep priority; candidate membership, Top3 recall and hard filters do not change. Projects with only code summaries, such as Olax, retain the first-stage order.

On the current local labels, this experimental second stage raised Sushigame Top1 from 10/20 to 12/20 while Top3 stayed 17/20. Its reserved validation half moved from 7/10 to 8/10 Top1. Adsivos stayed 10/20 Top1 and 12/20 Top3; AtlasBrain and Olax did not gain. The weight and gate were examined against these small datasets, so these figures are regression checks, not an independent estimate of future quality. Run `python benchmarks/evaluate_rerank.py <project>` against new reviewed questions before broadening the policy.

## Recover interrupted renames

1. Call `pending_operations()` and inspect an operation with `pending_operations(operation_id="rename-...")`.
2. Review source, destination and conflicts.
3. Call `recover_operation(operation_id=..., action="complete" or "reverter", recovery_revision=...)`.

The revision covers the journal and current file contents. Unknown external edits block recovery. Completing writes the destination/backlinks before removing the old path; reverting restores the old path/backlinks before removing the new path. Recovery remains journalled if interrupted again. Other interrupted write receipts are listed for manual inspection; they are not blindly replayed and do not have automatic rollback. The local journal is private but contains file contents.

## Stale summaries

`summary_queue()` derives the queue from persisted source hashes. After a successful queued index pass, the daemon refreshes at most one stale extractive summary using its existing index thread. `refresh_summaries()` runs one manually. Missing sources block refresh. Agent-authored summaries are never replaced with extraction: read the sources and submit a reviewed summary and current revisions through `consolidate_memory`. Sources remain intact. This is bounded extractive consolidation, not autonomous generative summarization.

## Indexing cost

A SQLite cache keyed by embedding model and exact input content reuses vectors when chunks are rebuilt. Duplicate inputs in the same batch share computation. The cache retains at most 50,000 entries and is local to each project. `index_vault` reports `cache_embeddings.calculados` and `reutilizados`. Existing indexed chunks populate the cache only when processed again. Model changes cannot reuse another model's vectors. Tests confirm a repeated forced index with identical content performs zero new embedding calls after the first cached pass.

Graph similarity can still require global recomputation; this change reduces embedding work, not the asymptotic cost of graph construction.

## Automatic capture quality

Separate transcript capture is disabled by default. The assistant records memory through MCP in the current conversation. See [optional assistant hooks](features-and-operation.md#optional-assistant-hooks) for explicit opt-in and history implications.

A nonblocking per-project lock allows only one extraction worker at a time. Stable per-item receipts prevent duplicate writes on retry; state files are replaced atomically. Existing-note discovery includes project-brain decisions and learnings. Exact normalized duplicates are recognized even with embeddings disabled; semantic matching still uses local embeddings when enabled.

`capture_quality()` returns counters for created, updated, duplicate, rejected and review-required items, plus the latest 100 review proposals. Automatic replacement proposals and detected numerical conflicts require explicit review; they no longer invalidate old decisions on their own. Review proposals remain in the local diagnostic queue; applying a manual decision does not automatically mark a proposal resolved. Counters cover capture after this upgrade, not all historical notes. Incomplete generic receipts still require inspection. The existing capture extractor may invoke the configured Claude/Codex CLI; these changes do not introduce another persistent daemon.

Restart the service after upgrading and reconnect MCP clients to discover new tool schemas.
