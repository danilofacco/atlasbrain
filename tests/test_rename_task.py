import json
from types import SimpleNamespace

import pytest

from atlasbrain import editor, renomear
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault
from atlasbrain.search import Searcher
from atlasbrain import inteligencia


def test_rename_preserves_incoming_outgoing_and_code(projeto):
    editor.save(projeto,'notes/Old.md','# Original title\n\n[relative](other.md#anchor)\n',create=True)
    editor.save(projeto,'notes/other.md','# Other\n',create=True)
    editor.save(projeto,'notes/ref.md','# Ref\n\n[[Old#Section|Display]]\n[old](Old.md#Section)\n`[[Old]]`\n\n```md\n[[Old]]\n```\n',create=True)
    index_vault(projeto,quiet=True)
    con=connect(projeto)
    try:
        plan=renomear.plan(projeto,con,'notes/Old.md','archive/New.md')
        before=(projeto/'notes/Old.md').read_bytes()
        assert (projeto/'notes/Old.md').read_bytes()==before
        result=renomear.apply(projeto,con,plan['de'],plan['para'],plan['revisao_plano'])
        assert result['renomeado'] and not (projeto/'notes/Old.md').exists()
        new=(projeto/'archive/New.md').read_text()
        assert 'atlasbrain_id:' in new and '# Original title' in new
        assert '[relative](../notes/other.md#anchor)' in new
        ref=(projeto/'notes/ref.md').read_text()
        assert '[[archive/New.md#Section|Display]]' in ref
        assert '[old](../archive/New.md#Section)' in ref
        assert '`[[Old]]`' in ref and '```md\n[[Old]]\n```' in ref
        assert con.execute('SELECT id FROM notes WHERE path=?',('archive/New.md',)).fetchone()[0]==plan['note_id']
    finally:
        con.close()


def test_rename_rejects_stale_preview_and_collision(projeto):
    editor.save(projeto,'Old.md','# Old',create=True)
    editor.save(projeto,'ref.md','[[Old]]',create=True)
    index_vault(projeto,quiet=True);con=connect(projeto)
    try:
        plan=renomear.plan(projeto,con,'Old.md','New.md')
        (projeto/'ref.md').write_text('[[Old]]\nmanual edit')
        with pytest.raises(editor.EditError,match='plano mudou'):
            renomear.apply(projeto,con,'Old.md','New.md',plan['revisao_plano'])
        assert not (projeto/'New.md').exists()
        with pytest.raises(editor.EditError):
            renomear.plan(projeto,con,'Old.md','ref.md')
        with pytest.raises(editor.EditError):
            renomear.plan(projeto,con,'Old.md','../outside.md')
    finally:
        con.close()


def test_task_focus_pending_and_budget(indexado):
    vault,con=indexado
    editor.save(vault,'Task.md','# Implementação\n\n- [ ] Conferir timeout de 30 segundos\n- [x] Já concluído\n',create=True)
    index_vault(vault,quiet=True)
    ctx=SimpleNamespace(vault=vault,con=con,searcher=Searcher(con))
    result=inteligencia.task_context(ctx,'soma',foco=['Task.md'],objetivo='revisar')
    assert result['arquivos'][0]['path']=='Task.md'
    assert result['arquivos'][0]['motivo']==['foco explícito']
    assert any('timeout' in p['tarefa'] for p in result['pendencias'])
    assert not any('concluído' in p['tarefa'] for p in result['pendencias'])
    small=inteligencia.budget_report(result,256)
    assert len(small)<=768 and json.loads(small)['truncado']
    assert json.loads(small)['omitidos_por_orcamento']


def test_interrupted_move_keeps_original_and_recovery_journal(projeto,monkeypatch):
    editor.save(projeto,'Old.md','# Original',create=True)
    editor.save(projeto,'ref.md','[[Old]]',create=True)
    index_vault(projeto,quiet=True);con=connect(projeto)
    try:
        plan=renomear.plan(projeto,con,'Old.md','New.md')
        save=editor.save
        def fail(vault,path,*args,**kwargs):
            if path=='ref.md':
                raise OSError('simulated disk failure')
            return save(vault,path,*args,**kwargs)
        monkeypatch.setattr(editor,'save',fail)
        with pytest.raises(editor.EditError,match='Registro de recuperação'):
            renomear.apply(projeto,con,'Old.md','New.md',plan['revisao_plano'])
        assert (projeto/'Old.md').exists() and (projeto/'New.md').exists()
        assert (projeto/'ref.md').read_text()=='[[Old]]'
        journals=list(editor._cache(projeto).glob('rename-*.json'))
        assert len(journals)==1 and json.loads(journals[0].read_text())['estado']=='pendente'
    finally:
        con.close()
