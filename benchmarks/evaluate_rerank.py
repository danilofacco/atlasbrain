"""Compare the two-stage search with its first-stage baseline on labelled projects."""

import argparse
import json
import time
from pathlib import Path

from atlasbrain.bench import carregar, metricas
from atlasbrain.db import connect, get_meta
from atlasbrain.search import Searcher


def evaluate(vault: Path) -> dict:
    questions = carregar(vault / '.atlasbrain' / 'benchmark.jsonl')
    con = connect(vault)
    try:
        revision = get_meta(con, 'rev')
        searcher = Searcher(con)
        searcher.search('aquecimento')
        variants = {}
        for name, enabled in (('first_stage', False), ('two_stage', True)):
            cases = []
            for question in questions:
                start = time.perf_counter()
                hits = searcher.search(question['pergunta'], limit=10, rerank=enabled)
                ms = (time.perf_counter() - start) * 1000
                rank = next((i + 1 for i, hit in enumerate(hits)
                             if hit['path'] in question['esperado']), None)
                cases.append({**question, 'rank': rank, 'ms': ms})
            splits = sorted({case['split'] for case in cases if case.get('split')})
            variants[name] = {'all': metricas(cases), **{
                split: metricas([case for case in cases if case.get('split') == split])
                for split in splits
            }}
        return {'project': vault.name, 'questions': len(questions),
                'index_stable': revision == get_meta(con, 'rev'), 'metrics': variants}
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vaults', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps([evaluate(path.resolve()) for path in args.vaults], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
