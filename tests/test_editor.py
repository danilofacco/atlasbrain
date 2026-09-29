from concurrent.futures import ThreadPoolExecutor

import pytest

from atlasbrain import editor
from atlasbrain.indexer import index_vault, scan


def test_edit_revision_backup_and_restore(indexado):
    vault,con=indexado
    before=editor.read(vault,'notas/Glossário.md')
    update=editor.save(vault,before['path'],'# Glossário\nAtualizado\n',before['revision'])
    assert update['recuperavel']
    backup=editor.read(vault,before['path'],previous=True)
    assert backup['content']==before['content']
    editor.save(vault,before['path'],backup['content'],update['revision'])
    assert editor.read(vault,before['path'])['content']==before['content']
    assert not any('.editor-backups' in path for path in scan(vault))


def test_external_changes_and_concurrent_saves_do_not_overwrite(indexado):
    vault,_=indexado
    data=editor.read(vault,'notas/Glossário.md')
    (vault/data['path']).write_text('External edit')
    with pytest.raises(editor.EditError) as error:
        editor.save(vault,data['path'],'Would overwrite',data['revision'])
    assert error.value.status==409
    assert (vault/data['path']).read_text()=='External edit'
    data=editor.read(vault,data['path'])
    def write(content):
        try:
            editor.save(vault,data['path'],content,data['revision'])
            return 'ok'
        except editor.EditError as error:
            return error.status
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(write,['First change','Second change']))
    assert sorted(result,key=str)==[409,'ok']


def test_create_collision_and_index_links(indexado):
    vault,con=indexado
    data=editor.save(vault,'.atlasbrain/Inbox/Nova.md','# Nova\n[[Glossário]]\n#teste\n',create=True)
    assert (vault/data['path']).exists()
    with pytest.raises(editor.EditError) as error:
        editor.save(vault,data['path'],'Do not overwrite',create=True)
    assert error.value.status==409
    index_vault(vault,quiet=True)
    assert con.execute('SELECT 1 FROM notes WHERE path=?',(data['path'],)).fetchone()
    assert con.execute("SELECT 1 FROM tags WHERE tag='teste'").fetchone()


@pytest.mark.parametrize('path',['../outside.md','/tmp/outside.md','src/app.py','.git/config.md','.atlasbrain/RELATORIO.md','.atlasbrain/.editor-backups/private.md','build/generated.md','.hidden.md','a//b.md'])
def test_reject_paths(indexado,path):
    vault,_=indexado
    with pytest.raises(editor.EditError):
        editor.save(vault,path,'x',create=True)


def test_symlinks_and_large_or_non_utf8_files(indexado,tmp_path):
    vault,_=indexado
    outside=tmp_path/'outside.md'
    outside.write_text('outside')
    (vault/'link.md').symlink_to(outside)
    with pytest.raises(editor.EditError): editor.read(vault,'link.md')
    with pytest.raises(editor.EditError): editor.save(vault,'link.md','no',create=True)
    (vault/'large.md').write_text('x'*(editor.MAX_BYTES+1))
    with pytest.raises(editor.EditError): editor.read(vault,'large.md')
    (vault/'encoding.md').write_bytes(b'\xff')
    with pytest.raises(editor.EditError): editor.read(vault,'encoding.md')
    assert outside.read_text()=='outside'


def test_links_use_complete_index_and_keep_duplicate_paths_distinct(indexado):
    vault,con=indexado
    for path in ['a/Plano.md','b/Plano.md','terceiro/Caderno.markdown']:
        file=vault/path;file.parent.mkdir(exist_ok=True);file.write_text('---\naliases: [Apelido]\n---\n# Plano\n')
    index_vault(vault,quiet=True)
    options=editor.link_candidates(con,'Plano')
    assert {'a/Plano','b/Plano'} <= {r['target'] for r in options}
    assert not any(r['path']=='a/Plano.md' for r in editor.link_candidates(con,'Plano','a/Plano.md'))
    assert editor.resolve_reference(con,'b/Plano#Seção|Texto')['path']=='b/Plano.md'
    assert editor.resolve_reference(con,'Apelido')['path']=='a/Plano.md'
    assert editor.resolve_reference(con,'Não existe') is None
    assert editor.resolve_reference(con,'src/app.py')['path']=='src/app.py'
    assert any(r['target']=='terceiro/Caderno' for r in editor.link_candidates(con,'Caderno'))
    assert len(editor.link_candidates(con,limit=2))==2
    # Code paths inserted by autocomplete must resolve in the graph as well.
    editor.save(vault,'a/Código.md','# Código\n[[src/app.py]]\n',create=True)
    index_vault(vault,quiet=True)
    assert con.execute("SELECT b.path FROM links l JOIN notes a ON a.id=l.src JOIN notes b ON b.id=l.dst WHERE a.path='a/Código.md'").fetchone()[0]=='src/app.py'


def test_delete_removes_file_and_index_without_trash(indexado):
    from pathlib import Path
    vault, con = indexado
    path = 'notas/Glossário.md'
    result = editor.delete(vault, path)
    assert not (vault / path).exists()
    assert result['permanente'] and 'recuperacao' not in result
    assert not list(editor._cache(vault).glob('trash-*'))
    assert not any('.editor-backups' in item for item in scan(vault))
    index_vault(vault, quiet=True)
    assert not con.execute('SELECT 1 FROM notes WHERE path=?', (path,)).fetchone()
    code = vault / 'sample.py'
    code.write_text('print(1)')
    editor.delete(vault, 'sample.py')
    assert not code.exists()


def test_delete_rejects_directories_traversal_internal_paths_and_symlinks(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('keep')
    (vault / 'link.md').symlink_to(outside)
    (vault / 'folder').mkdir()
    for path in ['../outside.md', str(outside), 'link.md', 'folder', '.git/config']:
        with pytest.raises(editor.EditError):
            editor.delete(vault, path)
    assert outside.read_text() == 'keep'
