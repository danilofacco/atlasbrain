# Search benchmarks

The search engine is measured with labelled questions. Each project's `.atlasbrain/benchmark.jsonl` contains a question, one or more expected file paths, and optionally a calibration/validation split. Labels must be checked against source files; search results never generate their own labels.

Run the two-stage ranking comparison on indexed projects:

```sh
uv run python benchmarks/evaluate_rerank.py /absolute/path/to/project [/absolute/path/to/another-project]
```

The report compares the same questions and index revision with the second stage off and on. It includes Top-1, Top-3, MRR, warmed latency and separate split metrics when available. Results print to the terminal; the runner does not modify the project or publish private queries. Use a fresh validation set before claiming a general improvement.

The isolated short-note regression fixture is in `search_fixture.json`:

```sh
uv run python -m benchmarks.evaluate_search_updates
```

This reproduces the earlier exact-name/status comparison. It writes `results/search-updates-2026-09-29.json` for inspection.

## Filter correctness and before/after performance

Compare the original 0.2.1 search engine with the current checkout:

```sh
uv run python -m benchmarks.evaluate_filters /absolute/path/to/project --before-ref 8cccda5 --repeats 5 --output /tmp/filter-comparison.json
```

Without project arguments it runs the isolated filter fixtures only. Project runs
require the embedding model to be cached locally; no model is fetched by the
harness. Set `ATLASBRAIN_NO_EMBED=1` for a lexical-only comparison. It uses SQLite
backup snapshots, keeps live indexes untouched, alternates before/after order,
and verifies deterministic results across repeats. Output contains aggregate
metrics and anonymous changed case positions, without private query text or
expected paths. Original reviewed questions, folder-scoped derivatives and
type-scoped derivatives are reported separately. Derived scopes use the reviewed
answer's metadata; they are not independent relevance labels or evidence of
better unrestricted natural-language search. See
[filter evaluation](../docs/search-filters-evaluation.md) for measured results.

## Bilingual intent pairs

`bilingual_public.jsonl` contains new paired Portuguese/English questions with
source hashes, reviewed evidence ranges and group-level calibration/validation
splits. Private multi-project cases stay in the local `.atlasbrain/benchmarks/`
directory, separate from previous benchmark labels. Translations and shared
labelled source files cannot cross splits.

```sh
uv run python -m benchmarks.evaluate_bilingual \
  --cases benchmarks/bilingual_public.jsonl \
  --vault atlasbrain=/absolute/path/to/atlasbrain \
  --repeats 3 --output /tmp/bilingual-public.json
```

Add `--before-ref 8cccda5` for the filter before/after comparison. A changed
labelled source or stale index stops the run; review evidence instead of silently
replacing labels. The existing model cache is required for hybrid measurement;
`ATLASBRAIN_NO_EMBED=1` selects a separately labelled lexical-only run. Reports
split positive ranking metrics from empty-scope correctness and include language,
project, query category and split slices. Use `--private-output` inside
`.atlasbrain/` to retain detailed diagnostic ranks and returned paths. The existing
MCP `avaliar_busca` schema is unchanged; run this paired/negative corpus with the
new evaluator rather than passing negative cases to its positive-only API. See
[the bilingual evaluation](../docs/search-bilingual-evaluation.md) for results and
limitations.

The [fresh performance analysis](../docs/search-performance-evaluation.md) reports positive-query latency separately from empty scopes, with five interleaved repetitions and a separate query-encoding profile.
