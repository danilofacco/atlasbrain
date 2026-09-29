import json

import numpy as np
import pytest

from atlasbrain import editor, renomear, consolidacao as c, captura, embed, indexer
from atlasbrain.db import connect
from atlasbrain.bench import avaliar_projeto


@pytest.mark.parametrize('action', ['concluir','reverter'])
def test_recover_interrupted_move(projeto,monkeypatch,action):
    editor.save(projeto,'Old.md','# Original',create=True)
    editor.save(projeto,'ref.md','[[Old]]',create=True)
    indexer.index_vault(projeto,quiet=True);con=connect(projeto)
    try:
        plan=renomear.plan(projeto,con,'Old.md','New.md');save=editor.save
        def fail(vault,path,*args,**kwargs):
            if path=='ref.md':
                raise OSError('disk failure')
            return save(vault,path,*args,**kwargs)
        with monkeypatch.context() as m:
            m.setattr(editor,'save',fail)
            with pytest.raises(editor.EditError):
                renomear.apply(projeto,con,'Old.md','New.md',plan['revisao_plano'])
        op=renomear.recovery(projeto,con)['operacoes'][0]['id']
        preview=renomear.recovery(projeto,con,op)
        (projeto/'ref.md').write_text('external edit')
        with pytest.raises(editor.EditError):
            renomear.recovery(projeto,con,op,action,preview['revisao_recuperacao'])
        assert (projeto/'ref.md').read_text()=='external edit'
        (projeto/'ref.md').write_text('[[Old]]')
        preview=renomear.recovery(projeto,con,op)
        result=renomear.recovery(projeto,con,op,action,preview['revisao_recuperacao'])
        assert result['estado']==('concluido' if action=='concluir' else 'revertido')
        assert (projeto/'New.md').exists()==(action=='concluir')
        assert (projeto/'Old.md').exists()==(action=='reverter')
        assert (projeto/'ref.md').read_text()==('[[New.md]]' if action=='concluir' else '[[Old]]')
        assert not renomear.recovery(projeto,con)['operacoes']
    finally:
        con.close()


@pytest.mark.parametrize('agent', [False,True])
def test_refresh_respects_summary_authorship(projeto,agent):
    for i in range(3):
        editor.save(projeto,f'notes/{i}.md',f'---\ntags: [fila]\n---\n# Nota {i}\n\nTimeout de {30+i} segundos.',create=True)
    indexer.index_vault(projeto,quiet=True);con=connect(projeto)
    try:
        group=c.plan(projeto,con)['grupos'][0]['grupo'];bundle=c.prepare(projeto,con,group)
        result=c.consolidate(projeto,group,'Resumo revisado com ressalvas próprias que devem ser preservadas.' if agent else None,
                             {s['path']:s['revision'] for s in bundle['fontes']} if agent else None)
        before=(projeto/result['path']).read_text()
        source=projeto/'notes/0.md';source.write_text(source.read_text()+'\n\nMudou para 90 segundos.')
        indexer.index_vault(projeto,quiet=True)
        queue=c.refresh_queue(projeto,execute=True)
        assert queue['fila'][0]['estado']==('revisao_agente' if agent else 'atualizado')
        if agent:
            assert (projeto/result['path']).read_text()==before
            with pytest.raises(editor.EditError,match='revisão explícita'):
                c.consolidate(projeto,group)
        else:
            assert '90 segundos' in (projeto/result['path']).read_text()
            assert not c.refresh_queue(projeto)['fila']
    finally:
        con.close()


def test_embedding_cache_reuses_chunks_and_invalidates_model(projeto,monkeypatch):
    calls=[]
    monkeypatch.setattr(embed,'EMBED_ENABLED',True)
    def vectors(texts):
        calls.extend(texts)
        return np.array([[1.,0.,0.] for _ in texts],dtype=np.float32)
    monkeypatch.setattr(embed,'embed',vectors)
    first=indexer.index_vault(projeto,quiet=True)
    assert first['cache_embeddings']['calculados']>0
    calls.clear()
    second=indexer.index_vault(projeto,force=True,quiet=True)
    assert not calls and second['cache_embeddings']['reutilizados']>0
    monkeypatch.setattr(indexer,'EMBED_MODEL','another-model')
    third=indexer.index_vault(projeto,force=True,quiet=True)
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


def test_capture_receipts_metrics_and_worker_lock(projeto,monkeypatch):
    import fcntl
    from atlasbrain.config import data_dir
    calls=[]
    def action():
        calls.append(True)
        return {'criada':False,'revisao_necessaria':True,'path':None}
    payload=['decisao','session',{'titulo':'Preço','decisao':'R$ 50'}]
    captura._capture_result(projeto,payload,action)
    captura._capture_result(projeto,payload,action)
    assert len(calls)==1 and captura.quality(projeto)['contadores']['revisar']==1
    assert len(captura.quality(projeto)['revisar'])==1
    monkeypatch.setattr(captura,'_worker',lambda *args: calls.append(False))
    with open(data_dir(projeto)/'capture.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        captura.worker(projeto,projeto/'missing','session','','Stop')
    assert calls==[True]


def test_evaluation_reports_context_and_rejects_invalid_labels(indexado):
    vault,con=indexado
    result=avaliar_projeto(vault,[{'pergunta':'src/util.py','esperado':['src/util.py']}],256)
    assert result['metricas']['top1']==1
    assert result['metricas']['recuperacao_50']==1
    assert result['diagnostico']['top_3']==1
    assert result['casos'][0]['rank_50']==1
    assert result['casos'][0]['tokens_estimados']<=256
    assert result['indice_estavel']
    assert (editor._cache(vault)/'benchmark-latest.json').exists()
    with pytest.raises(ValueError,match='ausente'):
        avaliar_projeto(vault,[{'pergunta':'x','esperado':['missing.md']}])
