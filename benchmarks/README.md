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
