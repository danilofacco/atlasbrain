import json

import numpy as np
import pytest

from atlasbrain import editor, rename, consolidation as c, capture, embed, indexer
from atlasbrain.db import connect
from atlasbrain.bench import evaluate_project


@pytest.mark.parametrize('action', ['concluir','reverter'])
def test_recover_interrupted_move(project,monkeypatch,action):
    editor.save(project,'Old.md','# Original',create=True)
    editor.save(project,'ref.md','[[Old]]',create=True)
    indexer.index_vault(project,quiet=True);con=connect(project)
    try:
        plan=rename.plan(project,con,'Old.md','New.md');save=editor.save
        def fail(vault,path,*args,**kwargs):
            if path=='ref.md':
                raise OSError('disk failure')
            return save(vault,path,*args,**kwargs)
        with monkeypatch.context() as m:
            m.setattr(editor,'save',fail)
            with pytest.raises(editor.EditError):
                rename.apply(project,con,'Old.md','New.md',plan['revisao_plano'])
        op=rename.recovery(project,con)['operacoes'][0]['id']
        preview=rename.recovery(project,con,op)
        (project/'ref.md').write_text('external edit')
        with pytest.raises(editor.EditError):
            rename.recovery(project,con,op,action,preview['revisao_recuperacao'])
        assert (project/'ref.md').read_text()=='external edit'
        (project/'ref.md').write_text('[[Old]]')
        preview=rename.recovery(project,con,op)
        result=rename.recovery(project,con,op,action,preview['revisao_recuperacao'])
        assert result['estado']==('concluido' if action=='concluir' else 'revertido')
        assert (project/'New.md').exists()==(action=='concluir')
        assert (project/'Old.md').exists()==(action=='reverter')
        assert (project/'ref.md').read_text()==('[[New.md]]' if action=='concluir' else '[[Old]]')
        assert not rename.recovery(project,con)['operacoes']
    finally:
        con.close()


@pytest.mark.parametrize('agent', [False,True])
def test_refresh_respects_summary_authorship(project,agent):
    for i in range(3):
        editor.save(project,f'notes/{i}.md',f'---\ntags: [fila]\n---\n# Nota {i}\n\nTimeout de {30+i} segundos.',create=True)
    indexer.index_vault(project,quiet=True);con=connect(project)
    try:
        group=c.plan(project,con)['grupos'][0]['grupo'];bundle=c.prepare(project,con,group)
        result=c.consolidate(project,group,'Resumo revisado com ressalvas próprias que devem ser preservadas.' if agent else None,
                             {s['path']:s['revision'] for s in bundle['fontes']} if agent else None)
        before=(project/result['path']).read_text()
        source=project/'notes/0.md';source.write_text(source.read_text()+'\n\nMudou para 90 segundos.')
        indexer.index_vault(project,quiet=True)
        queue=c.refresh_queue(project,execute=True)
        assert queue['fila'][0]['estado']==('revisao_agente' if agent else 'atualizado')
        if agent:
            assert (project/result['path']).read_text()==before
            with pytest.raises(editor.EditError,match='revisão explícita'):
                c.consolidate(project,group)
        else:
            assert '90 segundos' in (project/result['path']).read_text()
            assert not c.refresh_queue(project)['fila']
    finally:
        con.close()


def test_embedding_cache_reuses_chunks_and_invalidates_model(project,monkeypatch):
    calls=[]
    monkeypatch.setattr(embed,'EMBED_ENABLED',True)
    def vectors(texts):
        calls.extend(texts)
        return np.array([[1.,0.,0.] for _ in texts],dtype=np.float32)
    monkeypatch.setattr(embed,'embed',vectors)
    first=indexer.index_vault(project,quiet=True)
    assert first['cache_embeddings']['calculados']>0
    calls.clear()
    second=indexer.index_vault(project,force=True,quiet=True)
    assert not calls and second['cache_embeddings']['reutilizados']>0
    monkeypatch.setattr(indexer,'EMBED_MODEL','another-model')
    third=indexer.index_vault(project,force=True,quiet=True)
    assert calls and third['cache_embeddings']['calculados']>0


def test_code_body_embeddings_are_bounded_and_overridable(indexado,monkeypatch):
    _,con=indexado
    monkeypatch.setattr(embed,'embed',lambda texts: np.array([[1.,0.,0.] for _ in texts],dtype=np.float32))
    body_count=con.execute("SELECT COUNT(*) FROM chunks c JOIN notes n ON n.id=c.note_id WHERE n.kind='codigo' AND c.ord>=0").fetchone()[0]
    assert body_count>0
    monkeypatch.setattr(indexer,'AUTO_CODE_CHUNK_LIMIT',body_count-1)
    indexer._backfill_embeddings(con)
    missing=lambda: con.execute("SELECT COUNT(*) FROM chunks c JOIN notes n ON n.id=c.note_id WHERE n.kind='codigo' AND c.ord>=0 AND c.embedding IS NULL").fetchone()[0]
    assert missing()==body_count
    monkeypatch.setattr(indexer,'AUTO_CODE_CHUNK_LIMIT',body_count)
    indexer._backfill_embeddings(con)
    assert missing()==0
    con.execute("UPDATE chunks SET embedding=NULL WHERE note_id IN (SELECT id FROM notes WHERE kind='codigo') AND ord>=0")
    monkeypatch.setenv('ATLASBRAIN_EMBED_CODIGO','resumo')
    indexer._backfill_embeddings(con)
    assert missing()==body_count
    monkeypatch.setattr(indexer,'AUTO_CODE_CHUNK_LIMIT',0)
    monkeypatch.setenv('ATLASBRAIN_EMBED_CODIGO','all')
    indexer._backfill_embeddings(con)
    assert missing()==0


def test_capture_receipts_metrics_and_worker_lock(project,monkeypatch):
    from atlasbrain import locking
    from atlasbrain.config import data_dir
    calls=[]
    def action():
        calls.append(True)
        return {'criada':False,'revisao_necessaria':True,'path':None}
    payload=['decisao','session',{'titulo':'Preço','decisao':'R$ 50'}]
    capture._capture_result(project,payload,action)
    capture._capture_result(project,payload,action)
    assert len(calls)==1 and capture.quality(project)['contadores']['revisar']==1
    assert len(capture.quality(project)['revisar'])==1
    monkeypatch.setattr(capture,'_worker',lambda *args: calls.append(False))
    with open(data_dir(project)/'capture.lock','a') as lock:
        locking.acquire(lock, blocking=False)
        capture.worker(project,project/'missing','session','','Stop')
    assert calls==[True]


def test_evaluation_reports_context_and_rejects_invalid_labels(indexado):
    vault,con=indexado
    result=evaluate_project(vault,[{'pergunta':'src/util.py','esperado':['src/util.py']}],256)
    assert result['metricas']['top1']==1
    assert result['metricas']['recuperacao_50']==1
    assert result['diagnostico']['top_3']==1
    assert result['casos'][0]['rank_50']==1
    assert result['casos'][0]['tokens_estimados']<=256
    assert result['indice_estavel']
    assert (editor._cache(vault)/'benchmark-latest.json').exists()
    with pytest.raises(ValueError,match='ausente'):
        evaluate_project(vault,[{'pergunta':'x','esperado':['missing.md']}])
