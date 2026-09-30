"""RELATORIO.md — a visão de arquitetura do projeto:
god nodes, subsistemas com rótulo sem LLM, conexões surpreendentes, o "porquê" do código,
decisões ligadas ao código e perguntas que o grafo responde bem."""

from .localization import text as _text, value as _value

import datetime as dt
import json
import posixpath
from collections import Counter

from . import graph as g
from .config import BRAIN

CONF_LABEL = {"EXTRACTED": "extraído", "INFERRED": "inferido", "AMBIGUOUS": "ambíguo", None: "—"}


def _top_dir(path: str | None) -> str:
    if not path:
        return ""
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(raiz)")


def _label_comunidade(G, membros) -> str:
    """Rótulo sem LLM: a pasta que domina o grupo + os nós mais conectados dele."""
    dirs = Counter(_top_dir(G.nodes[m].get("path")) for m in membros)
    pasta, n = dirs.most_common(1)[0]
    top = sorted(membros, key=lambda m: G.degree(m), reverse=True)[:3]
    nomes = ", ".join(posixpath.basename(G.nodes[m]["path"] or G.nodes[m]["label"]) for m in top)
    return f"{pasta}" + ("" if n / len(membros) > 0.6 else _text(' (misto)')) + f" · {nomes}"


def gerar(con, vault) -> str:
    G = g.build_graph(con, similar=True, tags=False, ghosts=False)
    reais = [n for n in G.nodes if isinstance(n, int)]
    if not reais:
        return _text('# atlasbrain — relatório\n\nAinda não há arquivos indexados.\n')
    comm = g.communities(G)
    grupos: dict[int, list] = {}
    for n, c in comm.items():
        grupos.setdefault(c, []).append(n)
    # Contagens vêm das relações originais; comunidades usam a projeção sem direção.
    # Similaridade é guardada uma vez no banco, apesar dos dois sentidos na consulta.
    original_edges = [dict(r) for r in con.execute("SELECT kind, conf FROM links WHERE dst IS NOT NULL")]
    conf_count = Counter(d.get("conf") or "EXTRACTED" for d in original_edges)
    kinds = Counter(d["kind"] for d in original_edges)
    n_sim = con.execute("SELECT COUNT(*) FROM simbolos").fetchone()[0]
    P = lambda n: G.nodes[n]["path"] or G.nodes[n]["label"]

    out = [f"{_text('# atlasbrain — relatório de ')}{vault.name}", "",
           f"{_text('_Gerado em ')}{dt.datetime.now():%d/%m/%Y %H:%M}{_text('. Arquivo local, recriado a cada indexação._')}", "",
           f"**{len(reais)}{_text(' arquivos · ')}{n_sim}{_text(' símbolos · ')}{len(original_edges)}{_text(' ligações entre arquivos** (')}{', '.join((f'{v} {_value(k)}' for k, v in kinds.most_common()))}{_text('). Procedência: ')}{conf_count.get('EXTRACTED', 0)}{_text(' extraídas do código/texto, ')}{conf_count.get('INFERRED', 0)}{_text(' inferidas, ')}{conf_count.get('AMBIGUOUS', 0)}{_text(' ambíguas.')}", ""]

    from .topologia import construir
    topology = construir(con, vault)
    symbol_edges = [d for a, b, d in topology.edges(data=True) if d['kind'] in ('chama', 'herda')
                    and topology.nodes[b]['kind'] == 'simbolo']
    local = sum(topology.nodes[a]['path'] == topology.nodes[b]['path']
                for a, b, d in topology.edges(data=True) if d['kind'] == 'chama'
                and topology.nodes[b]['kind'] == 'simbolo')
    out += [f"{_text('**Grafo de símbolos:** ')}{len(symbol_edges)}{_text(' chamadas/heranças resolvidas (')}{local}{_text(' chamadas no mesmo arquivo); ')}{len(topology.graph['ambiguas'])}{_text(' referências ambíguas sem aresta.')}",
            _text('_Direção e relações paralelas são preservadas nas consultas; comunidades e interface usam a projeção por arquivos._'), ""]

    out.append(_text('## God nodes — por onde tudo passa'))
    gods = g.hubs(G, 10)
    for n in gods:
        viz = Counter(G[n][m]["kind"] for m in G[n])
        out.append(f"- `{P(n)}` — {G.degree(n)}{_text(' conexões (')}{', '.join((f'{v} {_value(k)}' for k, v in viz.most_common(3)))})")

    out += ["", _text('## Subsistemas (comunidades)')]
    shown = 0
    for c, membros in sorted(grupos.items(), key=lambda kv: -len(kv[1])):
        if len(membros) < 3 or shown >= 12:
            continue
        shown += 1
        out.append(f"- **{_label_comunidade(G, membros)}** — {len(membros)}{_text(' arquivos')}")

    out += ["", _text('## Conexões surpreendentes'), _text('_Ligações entre partes distantes do projeto (outra pasta e outro subsistema)._')]
    surpresas = []
    for a, b, d in G.edges(data=True):
        if not (isinstance(a, int) and isinstance(b, int)):
            continue
        pa, pb = G.nodes[a]["path"], G.nodes[b]["path"]
        score = (_top_dir(pa) != _top_dir(pb)) * 2 + (comm.get(a) != comm.get(b)) * 2
        score += {"menciona": 1.5, "similar": 1, "herda": 1, "chama": 0.5}.get(d["kind"], 0)
        if G.nodes[a]["kind"] != G.nodes[b]["kind"]:
            score += 1  # nota ↔ código
        if score >= 4:
            surpresas.append((score, pa, pb, d))
    for score, pa, pb, d in sorted(surpresas, key=lambda x: -x[0])[:8]:
        det = f" — {d.get('detalhe')}" if d.get("detalhe") and d["kind"] != "similar" else ""
        out.append(f"- `{pa}` ↔ `{pb}` · {_value(d['kind'])} ({CONF_LABEL.get(d.get('conf'))}){det}")
    if not surpresas:
        out.append(_text('- nenhuma por enquanto'))

    porques = con.execute(
        "SELECT n.path, p.etiqueta, p.texto, p.linha FROM porques p JOIN notes n ON n.id=p.note_id "
        "WHERE p.etiqueta NOT IN ('TODO') ORDER BY CASE p.etiqueta WHEN 'WHY' THEN 0 WHEN 'DECISION' THEN 0 "
        "WHEN 'NOTE' THEN 1 WHEN 'HACK' THEN 2 ELSE 3 END LIMIT 15").fetchall()
    todo = con.execute("SELECT COUNT(*) FROM porques WHERE etiqueta IN ('TODO','FIXME')").fetchone()[0]
    out += ["", _text('## O porquê no código'), _text('_Comentários NOTE / WHY / HACK / DECISION extraídos da AST._')]
    out += [f"- `{r['path']}:{r['linha']}` **{r['etiqueta']}** {r['texto']}" for r in porques] or [_text('- nenhum encontrado')]
    if todo:
        out.append(f"{_text('- …e ')}{todo}{_text(' TODO/FIXME espalhados (use `explicar` num arquivo para ver os dele)')}")

    dec = con.execute(
        "SELECT n.path, n.title, d.path alvo FROM links l JOIN notes n ON n.id=l.src JOIN notes d ON d.id=l.dst "
        "WHERE l.kind='menciona' AND json_extract(n.frontmatter,'$.tipo')='decisao' "
        "AND coalesce(json_extract(n.frontmatter,'$.status'),'ativa')='ativa' LIMIT 20").fetchall()
    if dec:
        out += ["", _text('## Decisões ligadas ao código')]
        out += [f"- [[{r['title']}]] → `{r['alvo']}`" for r in dec]

    out += ["", _text('## Perguntas que o grafo responde bem')]
    qs = []
    if len(gods) >= 2:
        qs.append(f"{_text('Como `')}{P(gods[0])}{_text('` se conecta a `')}{P(gods[1])}{_text('`? → `caminho`')}")
        qs.append(f"{_text('O que depende de `')}{P(gods[0])}{_text('` e o que quebra se eu mudar? → `explicar`')}")
    if surpresas:
        s = sorted(surpresas, key=lambda x: -x[0])[0]
        qs.append(f"{_text('Por que `')}{s[1]}{_text('` está ligado a `')}{s[2]}{_text('`? → `explicar`')}")
    if dec:
        qs.append(f"{_text('Quais decisões afetam `')}{_top_dir(dec[0]['alvo'])}{_text('`? → `decisoes` + `explicar`')}")
    qs.append(_text('Onde está definido `<símbolo>` e quem usa? → `onde`'))
    out += [f"- {q}" for q in qs[:5]]
    return "\n".join(out) + "\n"


def escrever(con, vault) -> str:
    texto = gerar(con, vault)
    from .editor import _atomic
    _atomic(vault / BRAIN / "RELATORIO.md", texto.encode("utf-8"))
    return texto
