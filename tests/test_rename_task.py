import json
from types import SimpleNamespace

import pytest

from atlasbrain import editor, rename
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault
from atlasbrain.search import Searcher
from atlasbrain import intelligence


def test_rename_preserves_incoming_outgoing_and_code(project):
    editor.save(project,'notes/Old.md','# Original title\n\n[relative](other.md#anchor)\n',create=True)
    editor.save(project,'notes/other.md','# Other\n',create=True)
    editor.save(project,'notes/ref.md','# Ref\n\n[[Old#Section|Display]]\n[old](Old.md#Section)\n`[[Old]]`\n\n```md\n[[Old]]\n```\n',create=True)
    index_vault(project,quiet=True)
    con=connect(project)
    try:
        plan=rename.plan(project,con,'notes/Old.md','archive/New.md')
        before=(project/'notes/Old.md').read_bytes()
        assert (project/'notes/Old.md').read_bytes()==before
        result=rename.apply(project,con,plan['de'],plan['para'],plan['revisao_plano'])
        assert result['renomeado'] and not (project/'notes/Old.md').exists()
        new=(project/'archive/New.md').read_text()
        assert 'atlasbrain_id:' in new and '# Original title' in new
        assert '[relative](../notes/other.md#anchor)' in new
        ref=(project/'notes/ref.md').read_text()
        assert '[[archive/New.md#Section|Display]]' in ref
        assert '[old](../archive/New.md#Section)' in ref
        assert '`[[Old]]`' in ref and '```md\n[[Old]]\n```' in ref
        assert con.execute('SELECT id FROM notes WHERE path=?',('archive/New.md',)).fetchone()[0]==plan['note_id']
    finally:
        con.close()


def test_rename_rejects_stale_preview_and_collision(project):
    editor.save(project,'Old.md','# Old',create=True)
    editor.save(project,'ref.md','[[Old]]',create=True)
    index_vault(project,quiet=True);con=connect(project)
    try:
        plan=rename.plan(project,con,'Old.md','New.md')
        (project/'ref.md').write_text('[[Old]]\nmanual edit')
        with pytest.raises(editor.EditError,match='plano mudou'):
            rename.apply(project,con,'Old.md','New.md',plan['revisao_plano'])
        assert not (project/'New.md').exists()
        with pytest.raises(editor.EditError):
            rename.plan(project,con,'Old.md','ref.md')
        with pytest.raises(editor.EditError):
            rename.plan(project,con,'Old.md','../outside.md')
    finally:
        con.close()


def test_task_focus_pending_and_budget(indexado):
    vault,con=indexado
    editor.save(vault,'Task.md','# Implementação\n\n- [ ] Conferir timeout de 30 segundos\n- [x] Já concluído\n',create=True)
    index_vault(vault,quiet=True)
    ctx=SimpleNamespace(vault=vault,con=con,searcher=Searcher(con))
    result=intelligence.task_context(ctx,'soma',focus=['Task.md'],objective='revisar')
    assert result['arquivos'][0]['path']=='Task.md'
    assert result['arquivos'][0]['motivo']==['foco explícito']
    assert any('timeout' in p['tarefa'] for p in result['pendencias'])
    assert not any('concluído' in p['tarefa'] for p in result['pendencias'])
    small=intelligence.budget_report(result,256)
    assert len(small)<=768 and json.loads(small)['truncado']
    assert json.loads(small)['omitidos_por_orcamento']


def test_interrupted_move_keeps_original_and_recovery_journal(project,monkeypatch):
    editor.save(project,'Old.md','# Original',create=True)
    editor.save(project,'ref.md','[[Old]]',create=True)
    index_vault(project,quiet=True);con=connect(project)
    try:
        plan=rename.plan(project,con,'Old.md','New.md')
        save=editor.save
        def fail(vault,path,*args,**kwargs):
            if path=='ref.md':
                raise OSError('simulated disk failure')
            return save(vault,path,*args,**kwargs)
        monkeypatch.setattr(editor,'save',fail)
        with pytest.raises(editor.EditError,match='Registro de recuperação'):
            rename.apply(project,con,'Old.md','New.md',plan['revisao_plano'])
        assert (project/'Old.md').exists() and (project/'New.md').exists()
        assert (project/'ref.md').read_text()=='[[Old]]'
        journals=list(editor._cache(project).glob('rename-*.json'))
        assert len(journals)==1 and json.loads(journals[0].read_text())['estado']=='pendente'
    finally:
        con.close()
