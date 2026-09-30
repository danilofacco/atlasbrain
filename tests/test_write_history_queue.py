import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from atlasbrain import editor
from atlasbrain.watch import IndexQueue


def test_section_history_revision_and_retry(project):
    path = 'section.md'
    content = '---\ntitle: Custom\ncustom: keep\n---\n# Note\n\n## Target\nold\n\n## Manual\nKeep this.\n'
    first = editor.save(project, path, content, create=True)
    result = editor.update_section(project, path, 'Target', 'new', first['revision'], 'op-1')
    assert result == editor.update_section(project, path, 'Target', 'new', first['revision'], 'op-1')
    current = editor.read(project, path)
    assert current['content'].startswith('---\ntitle: Custom\ncustom: keep\n---')
    assert '## Manual\nKeep this.' in current['content']
    with pytest.raises(editor.EditError):
        editor.update_section(project, path, 'Target', 'wrong', first['revision'], 'op-1')
    versions = editor.history(project, path)['versoes']
    assert len(versions) == 1
    diff = editor.history(project, path, versions[0]['id'])
    assert '+old' in diff['diff'] and '-new' in diff['diff']
    with pytest.raises(editor.EditError):
        editor.restore(project, path, versions[0]['id'], first['revision'])
    editor.restore(project, path, versions[0]['id'], current['revision'])
    assert editor.read(project, path)['content'] == content
    assert len(editor.history(project, path)['versoes']) == 2


def test_section_ignores_code_blocks_and_rejects_ambiguous_headings():
    text = '## Real\na\n```md\n## Fake\n```\n## Manual\nkeep\n'
    result = editor.section_content(text, 'Real', 'new')
    assert result == '## Real\n\nnew\n\n## Manual\nkeep\n'
    with pytest.raises(editor.EditError):
        editor.section_content('## A\na\n## A\nb\n', 'A', 'new')


def test_delete_removes_recovery_versions_without_touching_other_files(project):
    first=editor.save(project, 'a.md', 'original', create=True)
    editor.save(project, 'a.md', 'changed', first['revision'])
    other=editor.save(project, 'b.md', 'keep', create=True)
    editor.save(project, 'b.md', 'also keep', other['revision'])
    editor.delete(project, 'a.md')
    assert not (project/'a.md').exists()
    assert not editor._backup(project,'a.md').exists()
    assert not editor.history(project,'a.md')['versoes']
    assert len(editor.history(project,'b.md')['versoes'])==1
    assert (project/'b.md').read_text()=='also keep'


def test_idempotency_concurrent_and_interrupted_operations(project):
    calls = []
    def run(_):
        return editor.operation(project, 'same', ['payload'], lambda: calls.append('write') or {'ok':True})
    with ThreadPoolExecutor(4) as pool:
        assert all(x == {'ok': True} for x in pool.map(run, range(4)))
    assert calls == ['write']
    def interrupted():
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):
        editor.operation(project, 'failed', [], interrupted)
    with pytest.raises(editor.EditError, match='interrompida'):
        editor.operation(project, 'failed', [], lambda: calls.append('wrong'))
    assert calls == ['write']


def test_history_retention(project):
    state = editor.save(project, 'a.md', '0', create=True)
    for i in range(35):
        state = editor.save(project, 'a.md', str(i+1), state['revision'])
    assert len(editor.history(project, 'a.md')['versoes']) == 30


def test_queue_coalesces_retries_and_detects_removal(tmp_path):
    file = tmp_path/'a.md'; file.write_text('1')
    now = [0]; calls = []; skip = [False]
    def index(vault):
        calls.append(vault)
        return {'skipped':skip[0]}
    q = IndexQueue(lambda v: {'a.md':file} if file.exists() else {}, index, lambda:now[0])
    q.step(tmp_path);now[0]=3;q.step(tmp_path)
    assert len(calls)==1
    now[0]=6;q.step(tmp_path);assert len(calls)==1
    file.write_text('22');q.step(tmp_path)
    now[0]=7;file.write_text('333');q.step(tmp_path)
    now[0]=8;q.step(tmp_path);assert len(calls)==1
    now[0]=10;q.step(tmp_path);assert len(calls)==2
    file.unlink();q.step(tmp_path)
    now[0]=13;skip[0]=True;q.step(tmp_path)
    now[0]=16;skip[0]=False;q.step(tmp_path)
    assert len(calls)==4
    now[0]=19;q.step(tmp_path);assert len(calls)==4


def test_exact_lookup_current_decisions_and_hard_filters(project):
    from atlasbrain.indexer import index_vault
    from atlasbrain.db import connect
    from atlasbrain.search import Searcher
    for path, status, text in [('old.md','substituída','Use Redis. ' * 8), ('new.md','ativa','Use Redis.')]:
        (project/path).write_text('---\ntitle: Queue policy\ntipo: decisao\nstatus: '+status+'\n---\n# Queue policy\n'+text)
    (project/'unrelated.md').write_text('# Another note\nnew.md new.md old.md Redis Redis Redis')
    index_vault(project,quiet=True)
    con=connect(project);searcher=Searcher(con)
    try:
        results=searcher.search('Queue policy')
        assert results[0]['path']=='new.md'
        assert searcher.search('old.md')[0]['path']=='old.md'
        assert searcher.search('Queue policy status:substituída')[0]['path']=='old.md'
        assert not searcher.search('old.md pasta:missing')
        assert not searcher.search('old.md tag:missing')
        assert results[0]['motivos'] and results[0]['exato']
    finally:
        con.close()


def test_mcp_idempotent_create_append_and_memory_review(indexado):
    import threading
    from types import SimpleNamespace
    from atlasbrain import tools
    from atlasbrain.indexer import index_vault
    vault,con=indexado
    ctx=SimpleNamespace(vault=vault,con=con,db_lock=threading.Lock(),reindex=lambda:index_vault(vault,quiet=True))
    first=tools.create_note(ctx,'Retry test','original',operation_id='create-1')
    assert tools.create_note(ctx,'Retry test','original',operation_id='create-1')==first
    path='.atlasbrain/Inbox/Retry test.md'
    current=editor.read(vault,path)
    first=tools.append_note(ctx,path,'append once',current['revision'],'append-1')
    assert tools.append_note(ctx,path,'append once',current['revision'],'append-1')==first
    assert editor.read(vault,path)['content'].count('append once')==1
    with pytest.raises(editor.EditError):
        tools.append_note(ctx,path,'stale write',current['revision'])
    for name,number in [('first','97'),('second','147')]:
        editor.save(vault,name+'.md','---\ntitle: Preço Pro\ntipo: decisao\nstatus: ativa\n---\n# Preço Pro\nR$ '+number,create=True)
    ctx.reindex()
    report=json.loads(tools.review_memory(ctx))
    assert report['total_conflitos']==1
    assert report['possiveis_conflitos'][0]['origem']=='INFERRED'
