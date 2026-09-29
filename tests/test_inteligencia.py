import json
import threading
from types import SimpleNamespace

from atlasbrain import inteligencia as i
from atlasbrain.db import get_meta
from atlasbrain.indexer import index_vault
from atlasbrain.search import Searcher
from atlasbrain.registro import registrar_decisao, revisar_decisao


def test_history_keeps_comparison_across_unchanged_scans(indexado):
    vault, con = indexado
    initial = get_meta(con, 'snapshot_atual')
    index_vault(vault, quiet=True)
    assert get_meta(con, 'snapshot_atual') == initial
    assert not i.changes(con)['disponivel']
    file = vault / 'src/util.py'
    file.write_text('def soma(a,b):\n    return a+b+1\n')
    index_vault(vault, quiet=True)
    report = i.changes(con)
    assert report['disponivel']
    assert {'tipo':'arquivo','acao':'alterado','path':'src/util.py'} in report['itens']
    index_vault(vault, quiet=True)
    assert i.changes(con) == report
    page = i.changes(con, limite=1)
    assert len(page['itens']) == 1


def test_freshness_and_pending_pagination(indexado):
    vault, con = indexado
    assert i.status(con, vault)['atualizado']
    (vault / 'src/new.py').write_text('def added(): pass\n')
    (vault / 'src/util.py').unlink()
    state = i.status(con, vault, limite=1)
    assert not state['atualizado'] and state['pendentes']==2 and state['mais']
    assert len(i.status(con, vault, limite=1, offset=1)['itens'])==1


def test_suggestions_do_not_mutate_and_declared_link_survives_reindex(indexado):
    vault, con = indexado
    before = con.execute('SELECT count(*) FROM links').fetchone()[0]
    suggestions = i.suggestions(con, 'src/busca.py')
    assert suggestions['sugestoes']
    assert all(s['origem']=='INFERRED' and s['evidencias'] for s in suggestions['sugestoes'])
    assert con.execute('SELECT count(*) FROM links').fetchone()[0] == before
    r = i.register_link(con, vault, 'src/busca.py','src/util.py','Revisado: contexto de busca e cálculo')
    assert (vault / r['path']).exists()
    assert i.register_link(con,vault,'src/busca.py','src/util.py','Mesmo vínculo')['path']==r['path']
    index_vault(vault,quiet=True)
    row=con.execute("SELECT conf FROM links WHERE kind='manual'").fetchall()
    assert len(row)==1 and row[0][0]=='DECLARED'
    index_vault(vault,force=True,quiet=True)
    assert con.execute("SELECT count(*) FROM links WHERE kind='manual'").fetchone()[0]==1
    from atlasbrain.graph import build_graph
    assert any(e['kind']=='manual' for _,_,e in build_graph(con).edges(data=True))


def test_link_rejects_unknown_and_self(indexado):
    import pytest
    vault, con=indexado
    with pytest.raises(ValueError): i.register_link(con,vault,'../outside','src/util.py','x')
    with pytest.raises(ValueError): i.register_link(con,vault,'src/util.py','src/util.py','x')


def test_context_budget_and_continuation(indexado):
    vault, con=indexado
    ctx=SimpleNamespace(con=con,vault=vault,searcher=Searcher(con),db_lock=threading.Lock())
    context=i.task_context(ctx,'soma')
    assert context['arquivos'] and context['indice']['atualizado']
    assert all('path' in x for x in context['arquivos'])
    output=i.budget_report(context,256)
    assert len(output)<=768 and json.loads(output)
    report=dict(offset=10, total=20, itens=[{'path':'x'*150} for _ in range(10)], mais=False)
    output=json.loads(i.budget_report(report,256))
    assert output['truncado'] and output['mais']
    assert output['proximo_offset']==10+len(output['itens'])


def test_context_uses_successor_instead_of_historical_decision(indexado):
    vault, con = indexado
    old = registrar_decisao(vault, 'Preço antigo do plano Pro', 'O plano Pro custa R$ 97 por mês.', forcar=True)
    new = registrar_decisao(vault, 'Preço vigente do plano Pro', 'O plano Pro custa R$ 147 por mês.', forcar=True)
    revisar_decisao(vault, old['path'], 'substituir', 'Preço alterado após nova aprovação.', new['path'])
    ctx = SimpleNamespace(con=con, vault=vault, searcher=Searcher(con), db_lock=threading.Lock())
    context = i.task_context(ctx, 'preço do plano Pro', foco=[old['path']])
    assert new['path'] in {d['path'] for d in context['decisoes']}
    assert old['path'] not in {d['path'] for d in context['decisoes']}
    assert old['path'] not in {d['path'] for d in context['dependencias']}
    assert context['decisoes_historicas'] == [{'path': old['path'], 'status': 'substituída', 'substituta': new['path']}]


def test_semantic_similarity_does_not_hide_link_candidates(indexado):
    _,con=indexado
    a=con.execute("SELECT id FROM notes WHERE path='src/busca.py'").fetchone()[0]
    b=con.execute("SELECT id FROM notes WHERE path='src/util.py'").fetchone()[0]
    con.execute("INSERT INTO links(src,target,dst,kind,weight,conf) VALUES(?,?,?,'similar',.8,'INFERRED')",(a,'src/util.py',b))
    assert any(r['path']=='src/util.py' for r in i.suggestions(con,'src/busca.py')['sugestoes'])
    con.execute("INSERT INTO links(src,target,dst,kind,conf) VALUES(?,?,?,'wikilink','EXTRACTED')",(a,'src/util.py',b))
    assert all(r['path']!='src/util.py' for r in i.suggestions(con,'src/busca.py')['sugestoes'])
