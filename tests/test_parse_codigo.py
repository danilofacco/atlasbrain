from pathlib import Path

from atlasbrain.codigo import extrair
from atlasbrain.parse import chunk_markdown, parse, split_frontmatter


def test_frontmatter_e_titulo():
    fm, body = split_frontmatter("---\ntitle: Olá\ntags: [a, b]\n---\n# Outro\ntexto")
    assert fm == {"title": "Olá", "tags": ["a", "b"]}
    assert body.startswith("# Outro")
    p = parse("---\ntitle: Olá\n---\n# Outro\n", Path("x.md"), "nota")
    assert p.title == "Olá"  # frontmatter vence o cabeçalho
    assert parse("# Cabeçalho\n", Path("x.md"), "nota").title == "Cabeçalho"
    assert parse("sem título", Path("nome-do-arquivo.md"), "nota").title == "nome-do-arquivo"


def test_frontmatter_invalido_nao_quebra():
    fm, body = split_frontmatter("---\n: [quebrado\n---\ncorpo")
    assert fm == {} and "corpo" in body


def test_links_e_tags_ignoram_codigo():
    txt = (
        "Veja [[Nota A]], [[Pasta/Nota B|apelido]], ![[img.png]] e [local](outra.md) e [site](https://x.com).\n"
        "#projeto #sub/tag #123 cor #fff\n"
        "```\n[[DentroDoCodigo]] #naoetag\n```\n`[[inline]]`\n"
    )
    p = parse(txt, Path("n.md"), "nota")
    alvos = {(t, k) for t, k in p.links}
    assert ("Nota A", "wikilink") in alvos
    assert ("Pasta/Nota B", "wikilink") in alvos
    assert ("outra.md", "mdlink") in alvos
    assert not any("DentroDoCodigo" in t or "inline" in t or "x.com" in t for t, _ in p.links)
    assert {"projeto", "sub/tag", "fff"} <= p.tags
    assert "123" not in p.tags and "naoetag" not in p.tags


def test_chunks_respeitam_cabecalhos_e_tamanho():
    body = "# A\n" + "palavra " * 400 + "\n## B\ncurto\n"
    chunks = chunk_markdown(body, max_chars=1200)
    assert all(len(t) <= 1250 for _, t in chunks)
    assert any(h == "A › B" for h, _ in chunks)
    assert len([c for c in chunks if c[0] == "A"]) >= 2


def test_extrai_python():
    src = (
        "import os\nfrom .db import connect\nfrom . import embed, util as u\n\n"
        "class Loja(Base):\n    def comprar(self):\n        return connect()\n\n"
        "def f():\n    # NOTE: explica o porquê\n    os.path.join('a')\n"
    )
    e = extrair(src, ".py")
    nomes = {(n, t, pai) for n, t, _, pai in e.simbolos}
    assert ("Loja", "classe", None) in nomes and ("comprar", "funcao", "Loja") in nomes and ("f", "funcao", None) in nomes
    mods = [m for m, _ in e.imports]
    assert "os" in mods and ".db" in mods and ".embed" in mods and ".util" in mods
    assert ("Loja", "Base") in [(c, b) for c, b, _ in e.herancas]
    chamadas = {n for _, n, _ in e.chamadas}
    assert "connect" in chamadas and "path.join" in chamadas  # receptor guardado: não é o join() do projeto
    assert e.porques and e.porques[0][0] == "NOTE"


def test_extrai_typescript():
    src = (
        'import { a } from "@/lib/a";\nconst fs = require("fs");\n'
        "export const soma = (x) => x + 1;\n"
        "export class Tela extends Base { mostrar() { this.log(); ajuda(); } }\n"
    )
    e = extrair(src, ".ts")
    assert {"@/lib/a", "fs"} <= {m for m, _ in e.imports}
    nomes = {n for n, *_ in e.simbolos}
    assert {"soma", "Tela", "mostrar"} <= nomes
    assert ("Tela", "Base") in [(c, b) for c, b, _ in e.herancas]
    chamadas = {n for _, n, _ in e.chamadas}
    assert "ajuda" in chamadas and "this.log" in chamadas


def test_linguagem_desconhecida():
    assert extrair("qualquer coisa", ".xyz") is None
