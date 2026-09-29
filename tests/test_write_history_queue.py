import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from atlasbrain import editor
from atlasbrain.watch import IndexQueue


def test_section_history_revision_and_retry(projeto):
    path = 'section.md'
    content = '---\ntitle: Custom\ncustom: keep\n---\n# Note\n\n## Target\nold\n\n## Manual\nKeep this.\n'
    first = editor.save(projeto, path, content, create=True)
    result = editor.update_section(projeto, path, 'Target', 'new', first['revision'], 'op-1')
    assert result == editor.update_section(projeto, path, 'Target', 'new', first['revision'], 'op-1')
    current = editor.read(projeto, path)
    assert current['content'].startswith('---\ntitle: Custom\ncustom: keep\n---')
    assert '## Manual\nKeep this.' in current['content']
    with pytest.raises(editor.EditError):
        editor.update_section(projeto, path, 'Target', 'wrong', first['revision'], 'op-1')
    versions = editor.history(projeto, path)['versoes']
    assert len(versions) == 1
    diff = editor.history(projeto, path, versions[0]['id'])
    assert '+old' in diff['diff'] and '-new' in diff['diff']
    with pytest.raises(editor.EditError):
        editor.restore(projeto, path, versions[0]['id'], first['revision'])
    editor.restore(projeto, path, versions[0]['id'], current['revision'])
    assert editor.read(projeto, path)['content'] == content
    assert len(editor.history(projeto, path)['versoes']) == 2


def test_section_ignores_code_blocks_and_rejects_ambiguous_headings():
    text = '## Real\na\n```md\n## Fake\n```\n## Manual\nkeep\n'
    result = editor.section_content(text, 'Real', 'new')
    assert result == '## Real\n\nnew\n\n## Manual\nkeep\n'
    with pytest.raises(editor.EditError):
        editor.section_content('## A\na\n## A\nb\n', 'A', 'new')


def test_delete_removes_recovery_versions_without_touching_other_files(projeto):
    first=editor.save(projeto, 'a.md', 'original', create=True)
    editor.save(projeto, 'a.md', 'changed', first['revision'])
    other=editor.save(projeto, 'b.md', 'keep', create=True)
    editor.save(projeto, 'b.md', 'also keep', other['revision'])
    editor.delete(projeto, 'a.md')
    assert not (projeto/'a.md').exists()
    assert not editor._backup(projeto,'a.md').exists()
    assert not editor.history(projeto,'a.md')['versoes']
    assert len(editor.history(projeto,'b.md')['versoes'])==1
    assert (projeto/'b.md').read_text()=='also keep'


def test_idempotency_concurrent_and_interrupted_operations(projeto):
    calls = []
    def run(_):
        return editor.operation(projeto, 'same', ['payload'], lambda: calls.append('write') or {'ok':True})
    with ThreadPoolExecutor(4) as pool:
        assert all(x == {'ok': True} for x in pool.map(run, range(4)))
    assert calls == ['write']
    def interrupted():
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):
        editor.operation(projeto, 'failed', [], interrupted)
    with pytest.raises(editor.EditError, match='interrompida'):
        editor.operation(projeto, 'failed', [], lambda: calls.append('wrong'))
    assert calls == ['write']


def test_history_retention(projeto):
    state = editor.save(projeto, 'a.md', '0', create=True)
    for i in range(35):
        state = editor.save(projeto, 'a.md', str(i+1), state['revision'])
    assert len(editor.history(projeto, 'a.md')['versoes']) == 30


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


def test_exact_lookup_current_decisions_and_hard_filters(projeto):
    from atlasbrain.indexer import index_vault
    from atlasbrain.db import connect
    from atlasbrain.search import Searcher
    for path, status, text in [('old.md','substituída','Use Redis. ' * 8), ('new.md','ativa','Use Redis.')]:
        (projeto/path).write_text('---\ntitle: Queue policy\ntipo: decisao\nstatus: '+status+'\n---\n# Queue policy\n'+text)
    (projeto/'unrelated.md').write_text('# Another note\nnew.md new.md old.md Redis Redis Redis')
    index_vault(projeto,quiet=True)
    con=connect(projeto);searcher=Searcher(con)
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
    from atlasbrain import ferramentas
    from atlasbrain.indexer import index_vault
    vault,con=indexado
    ctx=SimpleNamespace(vault=vault,con=con,db_lock=threading.Lock(),reindex=lambda:index_vault(vault,quiet=True))
    first=ferramentas.criar_nota(ctx,'Retry test','original',operacao='create-1')
    assert ferramentas.criar_nota(ctx,'Retry test','original',operacao='create-1')==first
    path='.atlasbrain/Inbox/Retry test.md'
    current=editor.read(vault,path)
    first=ferramentas.anexar(ctx,path,'append once',current['revision'],'append-1')
    assert ferramentas.anexar(ctx,path,'append once',current['revision'],'append-1')==first
    assert editor.read(vault,path)['content'].count('append once')==1
    with pytest.raises(editor.EditError):
        ferramentas.anexar(ctx,path,'stale write',current['revision'])
    for name,number in [('first','97'),('second','147')]:
        editor.save(vault,name+'.md','---\ntitle: Preço Pro\ntipo: decisao\nstatus: ativa\n---\n# Preço Pro\nR$ '+number,create=True)
    ctx.reindex()
    report=json.loads(ferramentas.revisar_memoria(ctx))
    assert report['total_conflitos']==1
    assert report['possiveis_conflitos'][0]['origem']=='INFERRED'
