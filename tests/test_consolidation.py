import json

import pytest

from atlasbrain import consolidation as c, editor
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault


def populate(vault, count=3):
    for i in range(count):
        editor.save(vault,f'notes/n{i}.md',f'---\ntitle: Nota {i}\ntags: [fila]\n---\n# Nota {i}\n\nTimeout de {i+30} segundos. Preservar esta ressalva.\n',create=True)
    index_vault(vault,quiet=True)


def test_consolidation_preserves_sources_and_invalidates_on_change(project):
    populate(project)
    con=connect(project)
    try:
        group=c.plan(project,con)['grupos'][0]['grupo']
        bundle=c.prepare(project,con,group)
        before={s['path']:(project/s['path']).read_bytes() for s in bundle['fontes']}
        result=c.consolidate(project,group)
        assert result['modo']=='extrativo'
        assert all((project/p).read_bytes()==raw for p,raw in before.items())
        covered,stale=c.coverage(con)
        assert set(covered)==set(before) and not stale
        assert not c.plan(project,con)['grupos']
        from atlasbrain.search import Searcher
        searcher=Searcher(con)
        assert any(r['path']==result['path'] for r in searcher.search('Timeout',limit=20))
        path=next(iter(before));snap=editor.read(project,path)
        editor.save(project,path,snap['content']+'\nMudou para 90 segundos.\n',snap['revision'])
        index_vault(project,quiet=True)
        covered,stale=c.coverage(con)
        assert not covered and result['path'] in stale
        found=searcher.search('Timeout',limit=20)
        old=next(r for r in found if r['path']==result['path'])
        assert any('desatualizada' in reason for reason in old['motivos'])
        assert not any('fonte disponível na síntese' in reason for r in found for reason in r['motivos'])
    finally:
        con.close()


def test_agent_summary_requires_all_current_revisions(project):
    populate(project)
    con=connect(project)
    group=c.plan(project,con)['grupos'][0]['grupo'];bundle=c.prepare(project,con,group);con.close()
    summary='As fontes descrevem timeouts distintos de 30, 31 e 32 segundos; manter os contextos separados.'
    with pytest.raises(editor.EditError):
        c.consolidate(project,group,summary,{})
    result=c.consolidate(project,group,summary,{s['path']:s['revision'] for s in bundle['fontes']})
    assert result['modo']=='agente' and summary in (project/result['path']).read_text()


def test_automatic_threshold_and_bounded_batch(project):
    populate(project,9)
    assert not c.automatic(project)['consolidado']
    con=connect(project)
    plan=c.plan(project,con,minimum=9);con.close()
    assert plan['automatico_indicado'] and len(plan['grupos'][0]['fontes'])==6
    result=c.consolidate(project,plan['grupos'][0]['grupo'])
    assert result['fontes']==6


def test_automatic_writes_one_group_and_agent_can_refine_it(project):
    populate(project,40)
    result=c.automatic(project)
    assert result['consolidado'] and result['fontes']==6
    con=connect(project)
    try:
        state=c.plan(project,con)
        assert state['fontes_cobertas']==6 and len(state['sinteses_atuais'])==1
        group=result['path'].split('/')[-1].removesuffix('.md')
        bundle=c.prepare(project,con,group)
        summary='As notas têm timeouts diferentes. Cada prazo precisa ser consultado no contexto da sua fonte.'
        refined=c.consolidate(project,group,summary,{s['path']:s['revision'] for s in bundle['fontes']})
        assert refined['path']==result['path'] and refined['modo']=='agente'
    finally:
        con.close()
