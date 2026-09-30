import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.evaluate_bilingual import load_cases, metrics, paired, validate_sources


def pair(*, group='g1', path='src/app.py', split='validacao'):
    evidence = dict(path=path, sha256='0'*64, linha_inicio=1, linha_fim=1, ancora='import')
    return [dict(id=group+'-'+lang, grupo=group, projeto='demo', idioma=lang,
                 split=split, tipo='codigo', pergunta='pergunta '+group if lang == 'pt' else 'question '+group,
                 esperado=[path], evidencias=[evidence]) for lang in ('pt', 'en')]


def save(tmp_path, rows):
    file = tmp_path/'questions.jsonl'
    file.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return file


def test_public_pairs_have_no_source_or_translation_split_leakage():
    rows = load_cases(Path(__file__).resolve().parents[1]/'benchmarks/bilingual_public.jsonl')
    assert len(rows) == 18
    assert {r['idioma'] for r in rows} == {'pt', 'en'}


@pytest.mark.parametrize('damage', ['split', 'labels', 'language', 'duplicate', 'empty', 'negative', 'escape'])
def test_reject_invalid_or_leaking_labels(tmp_path, damage):
    rows = copy.deepcopy(pair())
    if damage == 'split': rows[1]['split'] = 'calibracao'
    elif damage == 'labels': rows[1]['esperado'] = ['src/other.py']
    elif damage == 'language': rows[1]['idioma'] = 'pt'
    elif damage == 'duplicate': rows[1]['id'] = rows[0]['id']
    elif damage == 'empty': rows[0]['esperado'] = []
    elif damage == 'negative':
        for row in rows:
            row.update(tipo='negativo', esperado=[], negativo={'filtro': 'pasta', 'valor': 'missing'})
    elif damage == 'escape':
        for row in rows:
            row['esperado'] = ['../outside.py']; row['evidencias'][0]['path'] = '../outside.py'
    with pytest.raises(ValueError): load_cases(save(tmp_path, rows))


def test_same_source_cannot_cross_splits(tmp_path):
    rows = pair(group='one', split='calibracao')+pair(group='two', split='validacao')
    with pytest.raises(ValueError, match='source appears in both'): load_cases(save(tmp_path, rows))


def test_negative_cases_do_not_inflate_positive_ranking():
    rows = [dict(negative=False, rank=1, empty=False, samples_ms=[4], scope_violations=0),
            dict(negative=False, rank=None, empty=True, samples_ms=[6], scope_violations=0),
            dict(negative=True, rank=None, empty=True, samples_ms=[1], scope_violations=0),
            dict(negative=True, rank=None, empty=False, samples_ms=[2], scope_violations=2)]
    result = metrics(rows)
    assert result['positives'] == result['negatives'] == 2
    assert result['top1'] == result['top3'] == result['recall10'] == result['mrr10'] == .5
    assert result['negative_empty_rate'] == .5
    assert result['scope_violations'] == 2
    assert metrics(rows[2:])['top1'] is None
    assert metrics([])['p50_ms'] is None


def test_pairs_count_language_disagreement_once_per_intent():
    rows = [dict(negative=False, project='demo', group='a', language='pt', rank=2),
            dict(negative=False, project='demo', group='a', language='en', rank=None),
            dict(negative=False, project='demo', group='b', language='pt', rank=1),
            dict(negative=False, project='demo', group='b', language='en', rank=3)]
    assert paired(rows) == dict(intents=2, both_top3=1, pt_only_top3=1, en_only_top3=0, neither_top3=0)


def test_stale_sources_and_nonempty_negative_scope_are_rejected(indexado):
    vault, con = indexado
    raw = (vault/'src/app.py').read_bytes()
    rows = pair()
    for row in rows:
        row['evidencias'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
    validate_sources(con, vault, rows)
    (vault/'src/app.py').write_text('changed')
    with pytest.raises(ValueError, match='Source changed'): validate_sources(con, vault, rows)
    negative = dict(id='n', tipo='negativo', negativo={'filtro': 'pasta', 'valor': 'src'})
    with pytest.raises(ValueError, match='no longer empty'): validate_sources(con, vault, [negative])


def test_unmeasured_filter_semantics_cannot_silently_pass(tmp_path):
    rows = pair()
    rows[0]['pergunta'] += ' desde:2026-09'
    with pytest.raises(ValueError, match='supports folder/type/tag/status'):
        load_cases(save(tmp_path, rows))


def test_aggregate_report_never_contains_private_questions_or_paths():
    from benchmarks.evaluate_bilingual import summaries
    rows = [dict(id='private-id', negative=False, project='demo', group='a', language=lang,
                 split='validacao', kind='codigo', rank=1, empty=False, scope_violations=0,
                 samples_ms=[1], pergunta='PRIVATE QUESTION', found_paths=['private/source.py'])
            for lang in ('pt', 'en')]
    encoded = json.dumps(summaries(rows))
    assert 'PRIVATE QUESTION' not in encoded
    assert 'private/source.py' not in encoded
    assert 'private-id' not in encoded
