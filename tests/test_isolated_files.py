import json
import threading
from types import SimpleNamespace

from atlasbrain import config, tools, graph, indexer
from atlasbrain.db import connect


def test_isolation_uses_full_index_and_ignores_self_links(project):
    folder = project / 'cases'
    folder.mkdir()
    contents = {
        'free.md': '# Livre\n', 'self.md': '# Própria\n',
        'tag.md': '---\ntags: [assunto]\n---\n# Etiquetada\n',
        'source.md': '# Origem\n[[target]]\n', 'target.md': '# Destino\n',
        'ghost.md': '# Referência pendente\n[[Ainda não existe]]\n',
        'semantic.md': '# Semântica\n', 'peer.md': '# Semelhante\n',
    }
    for name, text in contents.items():
        (folder / name).write_text(text)
    indexer.index_vault(project, quiet=True)
    con = connect(project)
    try:
        ids = {r['path']: r['id'] for r in con.execute('SELECT path,id FROM notes')}
        con.execute("INSERT INTO links(src,target,dst,kind,weight,conf) VALUES(?,?,?,?,?,?)",
                    (ids['cases/self.md'], 'cases/self.md', ids['cases/self.md'], 'wikilink', 1, 'EXTRACTED'))
        con.execute("INSERT INTO links(src,target,dst,kind,weight,conf) VALUES(?,?,?,?,?,?)",
                    (ids['cases/semantic.md'], 'cases/peer.md', ids['cases/peer.md'], 'similar', .9, 'INFERRED'))
        con.commit()
        report = graph.isolated_report(con, 'cases')
        assert {r['path'] for r in report['itens']} == {'cases/free.md', 'cases/self.md'}
        assert report['total'] == 2
        page = graph.isolated_report(con, 'cases', limit=1)
        assert len(page['itens']) == 1 and page['mais']
        assert not graph.isolated_report(con, 'cases', limit=1, offset=1)['mais']
        for tags in [True, False]:
            G = graph.build_graph(con, similar=False, tags=tags, ghosts=False)
            assert G.nodes[ids['cases/free.md']]['isolated']
            assert not G.nodes[ids['cases/tag.md']]['isolated']
            assert not G.nodes[ids['cases/semantic.md']]['isolated']
            view = graph.view(G)
            assert next(n for n in view['nodes'] if n['path'] == 'cases/free.md')['isolated']
    finally:
        con.close()


def test_global_mcp_soltos_lists_registered_projects_with_stable_pagination(tmp_path):
    global_brain = config.GLOBAL_BRAIN
    global_brain.mkdir()
    global_con = connect(global_brain)
    try:
        for name, paths in [('alpha', ['notes/a.md', 'notes/b.md']),
                            ('beta', ['notes/c.md'])]:
            vault = tmp_path / name
            vault.mkdir()
            con = connect(vault)
            try:
                for path in paths:
                    con.execute('INSERT INTO notes(path,title,kind) VALUES(?,?,?)',
                                (path, path, 'nota'))
                con.commit()
            finally:
                con.close()
        ctx = SimpleNamespace(vault=global_brain, con=global_con, db_lock=threading.Lock())
        first = json.loads(tools.isolated_files(ctx, folder='notes', limit=2))
        second = json.loads(tools.isolated_files(ctx, folder='notes', limit=2, offset=2))
        assert first['escopo'] == 'todos_projetos'
        assert first['total'] == second['total'] == 3
        assert first['mais'] and not second['mais']
        assert [(r['projeto'], r['path']) for r in first['itens'] + second['itens']] == [
            ('alpha', 'notes/a.md'), ('alpha', 'notes/b.md'), ('beta', 'notes/c.md')]
        assert {r['projeto']: r['total'] for r in first['projetos']} == {
            'alpha': 2, 'beta': 1, global_brain.name: 0}
        assert first['avisos'] == []
    finally:
        global_con.close()
