import json

import pytest

from atlasbrain import consolidacao as c, editor
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault


def populate(vault, count=3):
    for i in range(count):
        editor.save(vault,f'notes/n{i}.md',f'---\ntitle: Nota {i}\ntags: [fila]\n---\n# Nota {i}\n\nTimeout de {i+30} segundos. Preservar esta ressalva.\n',create=True)
    index_vault(vault,quiet=True)


def test_consolidation_preserves_sources_and_invalidates_on_change(projeto):
    populate(projeto)
    con=connect(projeto)
    try:
        group=c.plan(projeto,con)['grupos'][0]['grupo']
        bundle=c.prepare(projeto,con,group)
        before={s['path']:(projeto/s['path']).read_bytes() for s in bundle['fontes']}
        result=c.consolidate(projeto,group)
        assert result['modo']=='extrativo'
        assert all((projeto/p).read_bytes()==raw for p,raw in before.items())
        covered,stale=c.coverage(con)
        assert set(covered)==set(before) and not stale
        assert not c.plan(projeto,con)['grupos']
        from atlasbrain.search import Searcher
        searcher=Searcher(con)
        assert any(r['path']==result['path'] for r in searcher.search('Timeout',limit=20))
        path=next(iter(before));snap=editor.read(projeto,path)
        editor.save(projeto,path,snap['content']+'\nMudou para 90 segundos.\n',snap['revision'])
        index_vault(projeto,quiet=True)
        covered,stale=c.coverage(con)
        assert not covered and result['path'] in stale
        found=searcher.search('Timeout',limit=20)
        old=next(r for r in found if r['path']==result['path'])
        assert any('desatualizada' in reason for reason in old['motivos'])
        assert not any('fonte disponível na síntese' in reason for r in found for reason in r['motivos'])
    finally:
        con.close()


def test_agent_summary_requires_all_current_revisions(projeto):
    populate(projeto)
    con=connect(projeto)
    group=c.plan(projeto,con)['grupos'][0]['grupo'];bundle=c.prepare(projeto,con,group);con.close()
    summary='As fontes descrevem timeouts distintos de 30, 31 e 32 segundos; manter os contextos separados.'
    with pytest.raises(editor.EditError):
        c.consolidate(projeto,group,summary,{})
    result=c.consolidate(projeto,group,summary,{s['path']:s['revision'] for s in bundle['fontes']})
    assert result['modo']=='agente' and summary in (projeto/result['path']).read_text()


def test_automatic_threshold_and_bounded_batch(projeto):
    populate(projeto,9)
    assert not c.automatic(projeto)['consolidado']
    con=connect(projeto)
    plan=c.plan(projeto,con,minimum=9);con.close()
    assert plan['automatico_indicado'] and len(plan['grupos'][0]['fontes'])==6
    result=c.consolidate(projeto,plan['grupos'][0]['grupo'])
    assert result['fontes']==6


def test_automatic_writes_one_group_and_agent_can_refine_it(projeto):
    populate(projeto,40)
    result=c.automatic(projeto)
    assert result['consolidado'] and result['fontes']==6
    con=connect(projeto)
    try:
        state=c.plan(projeto,con)
        assert state['fontes_cobertas']==6 and len(state['sinteses_atuais'])==1
        group=result['path'].split('/')[-1].removesuffix('.md')
        bundle=c.prepare(projeto,con,group)
        summary='As notas têm timeouts diferentes. Cada prazo precisa ser consultado no contexto da sua fonte.'
        refined=c.consolidate(projeto,group,summary,{s['path']:s['revision'] for s in bundle['fontes']})
        assert refined['path']==result['path'] and refined['modo']=='agente'
    finally:
        con.close()
