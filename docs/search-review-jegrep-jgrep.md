# Search review: jegrep, jgrep and AtlasBrain

Reviewed on September 30, 2026. This is a source review and an experimental plan; the suggested engine changes have not been implemented.

## Sources and scope

Public source snapshots:

- [can1357/jegrep](https://github.com/can1357/jegrep/tree/197e674285eb321e2be4b5f6797d755af2c32c77): Rust, commit `197e674`.
- [kyu1204/jgrep](https://github.com/kyu1204/jgrep/tree/fedcb464f3be7aafc96c53d2d15e24e68cd22e8b): TypeScript, commit `fedcb46`.
- AtlasBrain: local main checkout, version 0.2.1, commit `8cccda5`.

The repositories were cloned into temporary directories for inspection. No competitor was installed globally, no real classifier API was called, and no private project contents were submitted to an external service. The local AtlasBrain MCP service was unavailable during this review, so AtlasBrain comparisons used its source directly.

## What is worth adopting

### jegrep: budgeted passage retrieval

Its [cascade implementation](https://github.com/can1357/jegrep/blob/197e674285eb321e2be4b5f6797d755af2c32c77/src/strategies/cascade.rs) first creates a lexical candidate pool, judges paths, scores small excerpts made from actual source lines, and verifies selected full passages. The source defaults include 128 candidates, 20 files, 384-byte sketches and a global cap of 40 full passages. Two strong lexical files retain slots in the file selection.

Crucially, sketches route the search; they do not count as verified source evidence. Its [window code](https://github.com/can1357/jegrep/blob/197e674285eb321e2be4b5f6797d755af2c32c77/src/strategies/window.rs) merges adjacent matching ranges and bounds reads. These are useful designs for MCP context selection, even without adopting its model.

### jgrep: behavioral queries and change-oriented tools

Its [search core](https://github.com/kyu1204/jgrep/blob/fedcb464f3be7aafc96c53d2d15e24e68cd22e8b/src/jgrep.ts) judges code chunks or Git diff hunks against a natural-language description and preserves file/line coordinates. Its [test selector](https://github.com/kyu1204/jgrep/blob/fedcb464f3be7aafc96c53d2d15e24e68cd22e8b/src/tests.ts) starts with cheap filename/import/package-entry matches, then scores remaining test signatures against a compact diff. Its pool isolates failures and preserves successful work.

Both normally use the Jev classification service through TypeSafe/OpenRouter, with configurable alternative endpoints. Reusing their classifier would introduce an external dependency or require a compatible local model. jgrep's README recommends English and warns that chunks are evaluated in isolation. Its behavior-search capability is therefore not evidence that the same accuracy would transfer to Portuguese or cross-file AtlasBrain questions.

## What AtlasBrain already has

`atlasbrain/search.py` combines FTS5/BM25, multilingual embeddings and symbol/path signals. It preserves exact hits, distinguishes implementation questions and discounts historical decisions and stale summaries. Its second stage reorders only the first three candidates and only when a small project's code-body vectors are complete.

`atlasbrain/parse.py` already provides 60-line code windows with eight-line overlap; line coordinates are encoded in headings. `ler(secao=...)` can read functions or Markdown sections. `contexto_tarefa` already assembles files, decisions and dependencies under a character-based token estimate. `impacto` already traverses extracted dependencies. Embedding reuse is keyed by model and content.

The opportunities are more precise evidence selection, better scope handling and new uses of the existing graph. Replacing indexing with a fresh repository scan for every query would lose useful incremental work and project memory.

## Confirmed findings and checks

1. **Filtered candidate starvation in AtlasBrain.** Search currently retrieves up to 200 candidates per signal before applying folder, tag and other constraints. An isolated, embeddings-disabled SQLite fixture with 205 equally matching documents outside `scope/` and one matching document inside it returned zero results for `needle pasta:scope`. The eligible document never reached the filter stage. This confirms a bounded-candidate problem, not that every filtered search fails. Apply eligible file/chunk constraints inside each retriever, or refill until the filtered candidate budget is satisfied. Explicit filters must remain strict.
2. **jgrep cache identity needs caution.** Despite its documentation, this snapshot's code-search cache key uses the constant default model, not `Options.model`. In a mocked two-model experiment, the second model reused the first model's probability without a new request. A future AtlasBrain relevance cache must include the effective model/ranker version, query, project identity, relevant source/context hashes and ranking configuration. This is not a finding against AtlasBrain's existing embedding cache.
3. **Local checks:** 68 jgrep tests passed across its chunking, diff/test-selection, error-isolation and pool suites; 17 AtlasBrain tests passed across search reranking, engine reliability and task context. These validate mechanics, not classifier accuracy. No jegrep Rust suite or live Jev benchmark was run.

## Recommended implementation order

| Priority | Change | Purpose and scope |
|---|---|---|
| 1 | Filter-aware retrieval | Fix the reproduced empty-result case before tuning ranking. Add regressions for folder/tag/type/status, late-ranked candidates and combined constraints. |
| 2 | Evidence-oriented results | Persist structured source start/end lines and chunk hashes. Return a few actual, deduplicated source ranges, their symbols and match reasons. Never invent original PDF line coordinates. |
| 3 | Broader second stage | Compare reranking 3, 10 and 20 candidate files using actual passages, query-concept coverage, symbols and implementation roles. Keep exact matches and hard filters. Test bounded on-demand passage scoring for large projects that have only summary vectors. |
| 4 | Budgeted context assembly | Select source passages before trimming output; merge overlaps and reserve room for relevant active decisions. Offer explicit bounded deeper retrieval when evidence is weak. |
| 5 | Git change search and test suggestions | Add a read-only diff query and test suggestions from extracted reverse dependencies, naming and imports. Report reasons, ambiguous edges and unsupported languages. Treat selection as a fast first signal, not a replacement for the full suite. |
| 6 | Optional learned verifier | Only after local improvements, compare a Portuguese-capable local relevance model on a held-out set. External Jev support, if desired later, should be optional and explicit. |

Query decomposition can help compound requests, but must retain their logical meaning. For example, finding an endpoint without authentication requires tracing middleware or configuration; an isolated chunk's missing auth call does not prove the endpoint is unprotected. Likewise, a normalized RRF score or cosine similarity must not be presented as a calibrated probability.

Suggested flow: project and filters → hybrid candidates → bounded source passage ranking → evidence ranges → context containing relevant current decisions and extracted dependencies. It can run within the existing service; it does not require another persistent process or port.

## Evaluation before promotion

Extend the existing labelled questions with Portuguese and English behavioral queries, compound questions, scoped searches, no-answer cases, changed files and project-isolation cases. Review expected file **and passage** ranges from source. Freeze a fresh validation split before tuning.

Compare the current engine, filter fix, passage ranking and broader reranking separately. Measure Top1/3, MRR, Recall@50, passage coverage, response size, cold/warm p50/p95, bytes read and false positives on explicitly labelled negative queries. Keep index revisions stable and account for index/model initialization separately. Do not increase global budgets based only on a few successful cases.

The [jegrep benchmark report](https://github.com/can1357/jegrep/blob/197e674285eb321e2be4b5f6797d755af2c32c77/benches/RESULTS.md) distinguishes file, region and line recall and acknowledges positive-only labels. The [jgrep test benchmark](https://github.com/kyu1204/jgrep/blob/fedcb464f3be7aafc96c53d2d15e24e68cd22e8b/bench/tests/README.md) uses test files edited by commit authors as its proxy. Neither measures AtlasBrain's Portuguese code/decision retrieval, and their published figures cannot be compared directly with our Top1/Top3 scores.
