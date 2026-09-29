from atlasbrain import graph, indexer
from atlasbrain.db import connect


def test_isolation_uses_full_index_and_ignores_self_links(projeto):
    folder = projeto / 'cases'
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
    indexer.index_vault(projeto, quiet=True)
    con = connect(projeto)
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
        page = graph.isolated_report(con, 'cases', limite=1)
        assert len(page['itens']) == 1 and page['mais']
        assert not graph.isolated_report(con, 'cases', limite=1, offset=1)['mais']
        for tags in [True, False]:
            G = graph.build_graph(con, similar=False, tags=tags, ghosts=False)
            assert G.nodes[ids['cases/free.md']]['isolated']
            assert not G.nodes[ids['cases/tag.md']]['isolated']
            assert not G.nodes[ids['cases/semantic.md']]['isolated']
            view = graph.vista(G)
            assert next(n for n in view['nodes'] if n['path'] == 'cases/free.md')['isolated']
    finally:
        con.close()
