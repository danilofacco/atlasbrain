import pytest
import json
import os
import time

import networkx as nx

from atlasbrain import graph as g
from atlasbrain.queries import find_files
from atlasbrain.indexer import index_vault, scan
from atlasbrain.search import Searcher

from .conftest import aresta


def paths(con):
    return {r[0] for r in con.execute("SELECT path FROM notes")}


# ------------------------------------------------------------------ indexação

def test_respeita_gitignore_e_inclui_atlasbrain(project):
    (project / ".atlasbrain" / "Decisions").mkdir(parents=True)
    (project / ".atlasbrain" / "Decisions" / "d.md").write_text("# Decisão\n")
    achados = set(scan(project))
    assert "build/gerado.py" not in achados  # .gitignore do projeto
    assert "src/app.py" in achados and ".atlasbrain/Decisions/d.md" in achados


def test_indexacao_incremental(indexado):
    project, con = indexado
    assert "src/app.py" in paths(con)
    s = index_vault(project, quiet=True)
    assert s["alterados"] == 0  # nada mudou → nada reprocessado
    f = project / "src" / "util.py"
    f.write_text("def soma(a, b):\n    return a + b + 0\n")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert index_vault(project, quiet=True)["alterados"] == 1
    f.unlink()
    assert index_vault(project, quiet=True)["removidos"] == 1
    assert "src/util.py" not in paths(con)


def test_cria_gitignore_do_cerebro(indexado):
    project, _ = indexado
    gi = (project / ".atlasbrain" / ".gitignore").read_text()
    assert "index.db*" in gi and "REPORT.md" in gi


# ------------------------------------------------------------------ resolução entre arquivos

def test_imports_resolvidos(indexado):
    _, con = indexado
    assert aresta(con, "src/app.py", "src/util.py", "importa")["conf"] == "EXTRACTED"
    assert aresta(con, "src/app.py", "src/db.py", "importa")  # from . import db
    assert aresta(con, "web/tela.ts", "lib/api.ts", "importa")  # alias @/ do tsconfig
    assert aresta(con, "web/tela.ts", "web/helpers.ts", "importa")  # relativo sem extensão


def test_chamadas_e_heranca(indexado):
    _, con = indexado
    c = aresta(con, "src/app.py", "src/util.py", "chama")
    assert c and c["conf"] == "EXTRACTED" and "soma()" in c["detalhe"]
    assert aresta(con, "src/app.py", "src/db.py", "chama")  # db.Banco() via módulo importado
    assert aresta(con, "web/tela.ts", "web/helpers.ts", "chama")
    assert aresta(con, "web/tela.ts", "lib/api.ts", "herda")


def test_sem_falsos_positivos(indexado):
    _, con = indexado
    # re.search() é da biblioteca padrão, não o search() de src/busca.py
    assert aresta(con, "src/app.py", "src/busca.py", "chama") is None
    # enviar() tem duas definições e nenhum import que desempate: ambíguo, não liga
    assert aresta(con, "web/tela.ts", "lib/a.ts", "chama") is None
    assert aresta(con, "web/tela.ts", "lib/b.ts", "chama") is None


def test_nota_que_cita_arquivo_liga_ao_codigo(indexado):
    _, con = indexado
    assert aresta(con, "notas/Arquitetura.md", "src/util.py", "menciona")["conf"] == "EXTRACTED"
    assert aresta(con, "notas/Glossário.md", "notas/Arquitetura.md", "wikilink")


def test_porques_extraidos(indexado):
    _, con = indexado
    r = con.execute("SELECT etiqueta, texto FROM porques").fetchall()
    assert ("WHY", "ponto de entrada do exemplo") in [(x[0], x[1]) for x in r]


# ------------------------------------------------------------------ consultas

def test_achar_arquivos_por_nome_e_simbolo(indexado):
    _, con = indexado
    assert find_files(con, "helpers")[0]["path"] == "web/helpers.ts"
    r = find_files(con, "conectar")
    assert r[0]["path"] == "src/db.py" and "conectar" in r[0]["simbolos"]
    assert find_files(con, "   ") == []


def test_busca_palavra_e_simbolo_sem_embeddings(indexado):
    _, con = indexado
    s = Searcher(con)
    assert s.search("glossário termos")[0]["path"] == "notas/Glossário.md"
    assert s.search("soma", signals=("struct",))[0]["path"] == "src/util.py"
    assert s.search("xyzinexistente") == []


# ------------------------------------------------------------------ grafo e visão

def test_caminho_guarda_a_direcao(indexado):
    _, con = indexado
    G = g.build_graph(con, similar=False, ghosts=False)
    ids = {d["path"]: n for n, d in G.nodes(data=True)}
    e = G[ids["src/app.py"]][ids["src/util.py"]]
    assert e["src"] == ids["src/app.py"]  # app importa util, não o contrário


def test_fantasmas_e_comunidades(indexado):
    _, con = indexado
    G = g.build_graph(con)
    assert "ghost:roadmap" in G  # [[Roadmap]] ainda não existe
    comm = g.communities(G)
    assert set(comm) == set(G.nodes)


def test_vista_pequena_mostra_tudo_e_grande_mostra_arquivos(indexado):
    _, con = indexado
    G = g.build_graph(con)
    v = g.view(G, "", limit=300)
    assert v["modo"] == "arquivos" and len(v["nodes"]) == G.number_of_nodes()
    pages = [g.view(G, "", limit=5, page=i) for i in range(g.view(G, "", limit=5)["paginas"])]
    paths = [n["path"] for page in pages for n in page["nodes"]]
    assert all(page["modo"] == "arquivos" and len(page["nodes"]) <= 5 for page in pages)
    assert len(paths) == len(set(paths)) == v["total"]
    assert all(n["kind"] != "pasta" for page in pages for n in page["nodes"])
    inside = g.view(G, "src", limit=5)
    assert any(n["path"] == "src/app.py" for n in inside["nodes"])


def test_vista_respeita_teto_de_arestas():
    G = nx.complete_graph(60)
    for n in G.nodes:
        G.nodes[n].update(label=str(n), path=f"p/{n}.py", kind="codigo", tipo=None)
    for a, b in G.edges:
        G[a][b].update(kind="chama", weight=1.0)
    v = g.view(G, "", limit=300, max_edges=100)
    assert len(v["edges"]) == 100 and v["ocultos"] == G.number_of_edges() - 100


def test_vista_paginas_distribuem_arquivos_e_foco():
    G = nx.Graph()
    for folder, count in (("big", 650), ("small", 20), ("docs", 30)):
        for i in range(count):
            path = f"{folder}/f{i:04}.md"
            G.add_node(path, label=path, path=path, kind="nota", tipo=None)
    # Uma decisão e um arquivo muito conectado devem aparecer cedo na própria área.
    G.nodes["big/f0649.md"]["tipo"] = "decisao"
    for i in range(30):
        G.add_edge("big/f0648.md", f"big/f{i:04}.md", kind="wikilink", weight=1)
    pages = [g.view(G, limit=120, page=i) for i in range(6)]
    assert all(p["modo"] == "arquivos" and len(p["nodes"]) <= 120 for p in pages)
    assert all(p["paginas"] == 6 and p["itens"] == 700 for p in pages)
    paths = [n["path"] for p in pages for n in p["nodes"]]
    assert len(paths) == len(set(paths)) == 700
    assert {n["path"].split("/")[0] for n in pages[0]["nodes"]} == {"big", "small", "docs"}
    assert "big/f0649.md" in [n["path"] for n in pages[0]["nodes"]]
    assert "big/f0648.md" in [n["path"] for n in pages[0]["nodes"]]
    assert g.view(G, limit=120, page=100)["pagina"] == 5
    focused = g.view(G, limit=120, focus="big/f0647.md")
    assert "big/f0647.md" in [n["path"] for n in focused["nodes"]]
    assert (focused["inicio"], focused["fim"]) == (focused["pagina"]*120+1, min(700, (focused["pagina"]+1)*120))


def test_relatorio(indexado):
    from atlasbrain import report
    project, con = indexado
    txt = report.write(con, project)
    assert "God nodes" in txt and "WHY" in txt and (project / ".atlasbrain" / "REPORT.md").exists()


def test_sinal_de_caminho_so_para_nome_exato(indexado):
    """Regressão do benchmark: palavra comum não pode casar com trecho de nome de arquivo."""
    _, con = indexado
    s = Searcher(con)
    assert s.search("helpers", signals=("struct",))[0]["path"] == "web/helpers.ts"
    assert s.search("help", signals=("struct",)) == []  # pedaço do nome não conta


def test_testes_e_docs_pesam_menos():
    from atlasbrain.search import _SECONDARY
    for p in ("__tests__/lib/a.test.ts", "docs/x.md", "scripts/y.ts", "src/app.test.tsx", "tests/test_a.py", "pkg/a_test.go"):
        assert _SECONDARY.search(p), p
    for p in ("lib/ai/rag.ts", "src/app.py", "app/api/route.ts", "notas/Arquitetura.md"):
        assert not _SECONDARY.search(p), p


def test_resumo_do_arquivo(indexado):
    from atlasbrain.code import path_words, file_summary
    assert path_words("lib/ai/promptHardening.ts") == "lib ai prompt hardening"
    r = file_summary("x/mod.py", '"""Explica o propósito deste módulo em detalhe suficiente."""\ndef f():\n    pass\n', ["f"])
    assert "propósito deste módulo" in r and "Define: f" in r
    _, con = indexado
    assert con.execute("SELECT COUNT(*) FROM chunks c JOIN notes n ON n.id=c.note_id "
                       "WHERE n.path='src/util.py' AND c.heading='resumo do arquivo'").fetchone()[0] == 1



def test_pula_lockfile_minificado_e_gerado(project):
    (project / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
    (project / "web" / "vendor.min.js").write_text("var a=1;" * 3000)
    (project / "src" / "api_pb2.py").write_text("# Generated by the protocol buffer compiler.  DO NOT EDIT!\nx = 1\n")
    (project / "web" / "bundle.js").write_text("var a=1;" * 2000)
    s = index_vault(project, quiet=True)
    from atlasbrain.db import connect, get_meta
    con = connect(project)
    p = paths(con)
    assert not ({"pnpm-lock.yaml", "web/vendor.min.js", "src/api_pb2.py", "web/bundle.js"} & p)
    assert "src/app.py" in p
    pulados = json.loads(get_meta(con, "pulados"))
    assert pulados["src/api_pb2.py"][0] == "gerado por ferramenta"
    assert index_vault(project, quiet=True)["alterados"] == 0  # não relê o que já sabe que pula


def test_versao_antiga_nao_reindexa_indice_novo(indexado, monkeypatch):
    from atlasbrain import indexer
    from atlasbrain.db import set_meta
    project, con = indexado
    set_meta(con, "schema", "99")
    con.commit()
    r = indexer.index_vault(project, quiet=True)
    assert r.get("skipped")  # processo antigo não pode sobrescrever o formato novo


# ------------------------------------------------------------------ menos tokens

def test_filtros_na_consulta(indexado):
    from atlasbrain.search import parse_filters
    q, f = parse_filters('boleto pasta:lib/ai tipo:codigo -teste "linha digitável" desde:2026-09')
    assert q == "boleto linha digitável"
    assert f == {"pasta": "lib/ai", "tipo": "codigo", "desde": "2026-09", "excluir": ["teste"], "frases": ["linha digitável"]}
    _, con = indexado
    s = Searcher(con)
    assert {r["path"] for r in s.search("glossário arquitetura tipo:nota")} <= {"notas/Glossário.md", "notas/Arquitetura.md"}
    assert all(r["path"].startswith("src/") for r in s.search("soma pasta:src"))
    assert not any(r["path"] == "notas/Glossário.md" for r in s.search("arquitetura -termos"))
    assert [r["path"] for r in s.search('"termos do projeto"')] == ["notas/Glossário.md"]


def test_resultado_tem_id_e_tokens(indexado):
    _, con = indexado
    r = Searcher(con).search("glossário")[0]
    assert isinstance(r["id"], int) and r["tokens"] > 0
    assert g.find_note(con, f"#{r['id']}")["path"] == r["path"]


def test_secao_de_nota_e_de_codigo(indexado):
    from atlasbrain.queries import extract_section
    project, con = indexado
    note = g.find_note(con, "notas/Arquitetura.md")
    txt = (project / "notas/Arquitetura.md").read_text()
    snippet, names = extract_section(con, note, txt, "arquitetura")
    assert snippet.startswith("# Arquitetura") and names == ["Arquitetura"]
    cod = g.find_note(con, "src/app.py")
    snippet, names = extract_section(con, cod, (project / "src/app.py").read_text(), "main")
    assert "def main" in snippet and "return soma" in snippet and "import re" not in snippet
    assert extract_section(con, cod, "", "nao_existe")[0] is None


# ------------------------------------------------------------------ visual: dependências e conexões

def test_mapa_de_dependencias(indexado):
    from atlasbrain.queries import dependencies
    _, con = indexado
    app = g.find_note(con, "src/app.py")
    d = dependencies(con, app["id"])
    side = {n["path"]: n["lado"] for n in d["nodes"]}
    assert side["src/app.py"] == 0 and side["src/util.py"] == 1 and side["src/db.py"] == 1
    util = g.find_note(con, "src/util.py")
    side = {n["path"]: n["lado"] for n in dependencies(con, util["id"])["nodes"]}
    assert side["src/app.py"] == -1  # quem usa fica do lado esquerdo
    assert all(e["kind"] in ("importa", "chama", "herda") for e in d["edges"])


@pytest.mark.embeddings
def test_conexoes_por_significado(project, com_embeddings):
    from atlasbrain.queries import connections
    from atlasbrain.db import connect
    index_vault(project, quiet=True)
    con = connect(project)
    arq = g.find_note(con, "notas/Arquitetura.md")
    r = connections(con, arq["id"], 5)
    assert r and all(x["path"] != "notas/Arquitetura.md" for x in r)
    assert r == sorted(r, key=lambda x: -x["score"]) and r[0]["trecho"]


def test_migra_indice_antigo_sem_reprocessar(indexado):
    """Formato antigo (texto duplicado no FTS, vetores float32) vira o novo no lugar."""
    import numpy as np
    from atlasbrain.db import connect
    project, con = indexado
    con.execute("DROP TABLE chunks_fts")
    con.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(title, heading, text, tokenize='unicode61 remove_diacritics 2')")
    con.execute("INSERT INTO chunks_fts(rowid, title, heading, text) SELECT id, title, heading, text FROM chunks")
    con.execute("UPDATE chunks SET embedding=? WHERE id=(SELECT MIN(id) FROM chunks)", (np.ones(384, np.float32).tobytes(),))
    con.execute("DELETE FROM meta WHERE key='vetores'")
    con.commit()
    con.close()
    con = connect(project)
    assert "content=" in con.execute("SELECT sql FROM sqlite_master WHERE name='chunks_fts'").fetchone()[0]
    assert len(con.execute("SELECT embedding FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]) == 384 * 2
    assert Searcher(con).search("glossário")[0]["path"] == "notas/Glossário.md"  # busca continua funcionando


def test_default_graph_page_size_and_custom_limits_keep_all_files():
    G = nx.Graph()
    for i in range(1051):
        path = f"src/f{i:04}.md"
        G.add_node(path, label=path, path=path, kind="nota", tipo=None)
    default = g.view(G)
    assert default["limite"] == 500 and len(default["nodes"]) == 500
    assert (default["paginas"], default["inicio"], default["fim"]) == (3, 1, 500)
    for limit in (50, 375, 500, 800):
        first = g.view(G, limit=limit)
        pages = [g.view(G, limit=limit, page=i) for i in range(first["paginas"])]
        paths = [n["path"] for page in pages for n in page["nodes"]]
        assert len(paths) == len(set(paths)) == 1051
        assert all(len(page["nodes"]) <= limit for page in pages)
        focused = g.view(G, limit=limit, focus="src/f1049.md")
        assert any(n["path"] == "src/f1049.md" for n in focused["nodes"])
