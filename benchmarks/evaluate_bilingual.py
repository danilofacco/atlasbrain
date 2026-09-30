"""Evaluate source-reviewed PT/EN intent pairs on frozen, read-only index copies.

Private queries, evidence and expected paths never enter the aggregate report.
Ranking metrics exclude negative filter cases. Source hashes detect stale labels.
"""
import argparse
import hashlib
import json
import sqlite3
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from atlasbrain import config, db, embed
from atlasbrain.search import Searcher, parse_filters
from benchmarks.evaluate_filters import baseline


def load_cases(file):
    rows = [json.loads(line) for line in file.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.startswith('//')]
    if not rows:
        raise ValueError('Empty question set')
    groups, ids, queries, sources = defaultdict(list), set(), set(), {}
    for row in rows:
        required = {'id', 'grupo', 'projeto', 'idioma', 'split', 'tipo', 'pergunta', 'esperado'}
        if not required <= row.keys() or row['idioma'] not in ('pt', 'en') or row['split'] not in ('calibracao', 'validacao'):
            raise ValueError('Invalid case metadata')
        if not isinstance(row['pergunta'], str) or not row['pergunta'].strip() or not isinstance(row['esperado'], list):
            raise ValueError('Invalid question or labels')
        if set(parse_filters(row['pergunta'])[1]) - {'pasta', 'tipo', 'tag', 'status'}:
            raise ValueError('This evaluator supports folder/type/tag/status scopes only')
        if row['id'] in ids or (row['projeto'], row['pergunta']) in queries:
            raise ValueError('Duplicate case or question')
        ids.add(row['id']); queries.add((row['projeto'], row['pergunta']))
        groups[(row['projeto'], row['grupo'])].append(row)
        if row['tipo'] == 'negativo':
            negative = row.get('negativo', {})
            key, value = negative.get('filtro'), negative.get('valor')
            if row['esperado'] or key not in ('pasta', 'tag') or not value:
                raise ValueError('A negative case must declare an empty folder/tag scope')
            if parse_filters(row['pergunta'])[1] != {key: value}:
                raise ValueError('Negative query and declared scope differ')
        else:
            evidence = row.get('evidencias', [])
            if not row['esperado'] or {e['path'] for e in evidence} != set(row['esperado']):
                raise ValueError('Every positive label needs source evidence')
            for path in row['esperado']:
                relative = Path(path)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Expected paths must stay inside the project')
                key = (row['projeto'], path)
                if key in sources and sources[key] != row['split']:
                    raise ValueError('The same labelled source appears in both splits')
                sources[key] = row['split']
    for pair in groups.values():
        if len(pair) != 2 or {r['idioma'] for r in pair} != {'pt', 'en'}:
            raise ValueError('Every intent needs exactly one PT and one EN case')
        for key in ('split', 'tipo', 'esperado', 'evidencias', 'negativo'):
            if pair[0].get(key) != pair[1].get(key):
                raise ValueError('Translations must share labels, evidence and split')
    return rows


def validate_sources(con, vault, cases):
    checked = set()
    for row in cases:
        if row['tipo'] == 'negativo':
            scope = row['negativo']; value = scope['valor']
            if scope['filtro'] == 'pasta':
                present = any(p[0].lower().startswith(value.lower().strip('/')+'/')
                              for p in con.execute('SELECT path FROM notes'))
            else:
                present = con.execute('SELECT 1 FROM tags WHERE tag=?', (value.lstrip('#').lower(),)).fetchone()
            if present:
                raise ValueError('Negative scope is no longer empty: '+row['id'])
            continue
        for evidence in row['evidencias']:
            path = evidence['path']
            evidence_key = (path, evidence['sha256'], evidence['linha_inicio'], evidence['linha_fim'], evidence['ancora'])
            if evidence_key in checked:
                continue
            target = (vault/path).resolve()
            if not target.is_relative_to(vault.resolve()):
                raise ValueError('Evidence escapes the project')
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() != evidence['sha256']:
                raise ValueError('Source changed; review labels before running: '+row['id'])
            lines = raw.decode('utf-8').splitlines()
            start, end = evidence['linha_inicio'], evidence['linha_fim']
            if not 1 <= start <= end <= len(lines) or evidence['ancora'] not in lines[start-1]:
                raise ValueError('Invalid reviewed evidence range: '+row['id'])
            indexed = con.execute('SELECT hash FROM notes WHERE path=?', (path,)).fetchone()
            if not indexed or indexed[0] != hashlib.sha1(raw).hexdigest():
                raise ValueError('Index does not match reviewed source: '+row['id'])
            checked.add(evidence_key)


def metadata_violation(con, query, path):
    """Check returned file metadata independently of the search candidate SQL."""
    _, filters = parse_filters(query)
    scope = filters.get('pasta', '').strip('/').lower()
    if scope and not path.lower().startswith(scope+'/'):
        return True
    note = con.execute('SELECT id,kind,frontmatter FROM notes WHERE path=?', (path,)).fetchone()
    if not note:
        return True
    fm = json.loads(note['frontmatter'] or '{}')
    tag = filters.get('tag')
    if tag and not con.execute('SELECT 1 FROM tags WHERE note_id=? AND tag=?',
                              (note['id'], tag.lstrip('#').lower())).fetchone():
        return True
    aliases = {'código': 'codigo', 'códigos': 'codigo', 'decisão': 'decisao',
               'decisões': 'decisao', 'decisoes': 'decisao'}
    value = filters.get('tipo')
    if value:
        value = aliases.get(value.lower(), value.lower().removesuffix('s'))
        if value not in (note['kind'], fm.get('tipo')):
            return True
    status = filters.get('status')
    return bool(status and str(fm.get('status', 'ativa')).casefold() != status.casefold())


def metrics(rows):
    positives = [r for r in rows if not r['negative']]
    negatives = [r for r in rows if r['negative']]
    n = len(positives)
    samples = sorted(t for row in rows for t in row['samples_ms'])
    return dict(cases=len(rows), positives=n, negatives=len(negatives),
                top1=sum(r['rank'] == 1 for r in positives)/n if n else None,
                top3=sum(r['rank'] is not None and r['rank'] <= 3 for r in positives)/n if n else None,
                recall10=sum(r['rank'] is not None for r in positives)/n if n else None,
                mrr10=sum(1/r['rank'] for r in positives if r['rank'])/n if n else None,
                negative_empty_rate=sum(r['empty'] for r in negatives)/len(negatives) if negatives else None,
                scope_violations=sum(r['scope_violations'] for r in rows),
                p50_ms=statistics.median(samples) if samples else None,
                p95_ms=samples[min(len(samples)-1, int(len(samples)*.95))] if samples else None)


def paired(rows):
    groups = defaultdict(dict)
    for row in rows:
        if not row['negative']:
            groups[(row['project'], row['group'])][row['language']] = row['rank'] is not None and row['rank'] <= 3
    counts = dict(both_top3=0, pt_only_top3=0, en_only_top3=0, neither_top3=0)
    for pair in groups.values():
        pt, en = pair['pt'], pair['en']
        key = 'both_top3' if pt and en else 'pt_only_top3' if pt else 'en_only_top3' if en else 'neither_top3'
        counts[key] += 1
    return dict(intents=len(groups), **counts)


def summaries(rows):
    slices = {dimension: {} for dimension in ('language', 'split', 'kind', 'project')}
    for dimension in slices:
        for value in sorted({row[dimension] for row in rows}):
            slices[dimension][value] = metrics([r for r in rows if r[dimension] == value])
    return dict(overall=metrics(rows), slices=slices, paired_top3=paired(rows))


def evaluate(vault, cases, directory, Before, repeats):
    source = sqlite3.connect((vault/'.atlasbrain/index.db').as_uri()+'?mode=ro', uri=True)
    con = sqlite3.connect(directory/(vault.name+'.db')); con.row_factory = sqlite3.Row
    try:
        source.backup(con)
    finally:
        source.close()
    try:
        validate_sources(con, vault, cases)
        if embed.enabled() and db.get_meta(con, 'embed_model') != config.EMBED_MODEL:
            raise ValueError('Index embedding model differs from the query model')
        revision = db.get_meta(con, 'rev')
        engines = {'current': Searcher(con)}
        if Before:
            engines['before'] = Before(con)
        collected = {name: [] for name in engines}
        for engine in engines.values():
            engine.search(cases[0]['pergunta'], limit=10)  # warm caches/model outside timing
        for position, case in enumerate(cases):
            paths, timings = {}, {name: [] for name in engines}
            for repeat in range(repeats):
                order = list(engines) if (position+repeat)%2 == 0 else list(reversed(engines))
                for name in order:
                    start = time.perf_counter()
                    hits = engines[name].search(case['pergunta'], limit=10)
                    timings[name].append((time.perf_counter()-start)*1000)
                    found = [h['path'] for h in hits]
                    if name in paths and paths[name] != found:
                        raise ValueError('Order changed on a frozen index')
                    paths[name] = found
            for name, found in paths.items():
                rank = next((i+1 for i, p in enumerate(found) if p in case['esperado']), None)
                collected[name].append(dict(id=case['id'], project=case['projeto'], group=case['grupo'],
                                            language=case['idioma'], split=case['split'], kind=case['tipo'],
                                            negative=case['tipo'] == 'negativo', rank=rank, empty=not found, found_paths=found,
                                            scope_violations=sum(metadata_violation(con, case['pergunta'], p) for p in found),
                                            samples_ms=timings[name]))
        return dict(project=cases[0]['projeto'], revision=revision, files=con.execute('SELECT COUNT(*) FROM notes').fetchone()[0],
                    index_stable=revision == db.get_meta(con, 'rev'),
                    model=db.get_meta(con, 'embed_model')), collected
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--vault', action='append', required=True, help='project-name=/absolute/project/path')
    parser.add_argument('--before-ref')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private-output', type=Path, help='Optional detailed ranks, inside the local brain only')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    vaults = {}
    for value in args.vault:
        name, sep, raw = value.partition('=')
        if not sep or not Path(raw).is_absolute() or name in vaults:
            parser.error('Use unique project-name=/absolute/project/path mappings')
        vaults[name] = Path(raw).resolve()
    cases = load_cases(args.cases)
    if {row['projeto'] for row in cases} != set(vaults):
        parser.error('Project mappings must match the question set exactly')
    if args.private_output and '.atlasbrain' not in args.private_output.resolve().parts:
        parser.error('Detailed results must stay inside .atlasbrain')
    # Freeze all labels before invoking search; fail on stale sources instead of silently changing answers.
    if embed.enabled():
        from fastembed import TextEmbedding
        embed._model = TextEmbedding(model_name=config.EMBED_MODEL, cache_dir=str(config.MODEL_CACHE), local_files_only=True)
    source_hash = hashlib.sha256(Path(__import__('atlasbrain.search', fromlist=['__file__']).__file__).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='atlasbrain-bilingual-') as temp:
        directory = Path(temp); before_sha, Before = baseline(args.before_ref, directory) if args.before_ref else (None, None)
        data, projects = defaultdict(list), []
        for name, vault in vaults.items():
            print('Evaluating '+name+'...', flush=True)
            metadata, collected = evaluate(vault, [r for r in cases if r['projeto'] == name], directory, Before, args.repeats)
            projects.append(metadata)
            for variant, rows in collected.items():
                data[variant].extend(rows)
        report = dict(dataset_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(), search_sha256=source_hash,
                      baseline_commit=before_sha, embedding_enabled=embed.enabled(), repeats=args.repeats,
                      cases=len(cases), intents=len(cases)//2, projects=projects,
                      protocol='Source-reviewed labels frozen before retrieval; PT/EN pairs and shared labelled sources stay in one split. '
                               'Positive any-labelled-file ranks at k=10; negative empty folder/tag scopes measured separately. '
                               'Warmed latency includes positive and negative queries on SQLite snapshots. Natural-language abstention and answer correctness are not measured.',
                      results={variant: summaries(rows) for variant, rows in data.items()})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        if args.private_output:
            args.private_output.parent.mkdir(parents=True, exist_ok=True)
            args.private_output.write_text(json.dumps(dict(report=report, details=data), ensure_ascii=False, indent=2)+'\n')
        for variant, result in report['results'].items():
            print(variant, json.dumps(result['slices']['language']))
        print('Aggregate report saved to '+str(args.output))


if __name__ == '__main__':
    main()
