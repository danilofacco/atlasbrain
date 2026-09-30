"""Grafo de conhecimento: notas, ligações explícitas, semânticas e tags."""

import json
import re
import posixpath
import heapq
import math

import networkx as nx

KIND_PRIORITY = {"manual": 3,"wikilink": 3, "mdlink": 3, "importa": 3, "herda": 3, "chama": 2.5, "menciona": 2.5,
                 "similar": 2, "tag": 1}


def find_note(con, ref: str):
    """Aceita caminho, nome do arquivo, título ou alias; cai para busca aproximada."""
    ref = ref.strip().strip("[]").strip()
    if re.fullmatch(r"#\d+", ref):  # id devolvido pelo `buscar`
        return con.execute("SELECT * FROM notes WHERE id=?", (int(ref[1:]),)).fetchone()
    low = ref.lower().removesuffix(".md")
    for sql, arg in (
        ("SELECT * FROM notes WHERE lower(path)=?", ref.lower()),
        ("SELECT * FROM notes WHERE lower(path)=?", low + ".md"),
        ("SELECT * FROM notes WHERE lower(title)=? ORDER BY length(path)", low),
    ):
        row = con.execute(sql, (arg,)).fetchone()
        if row:
            return row
    rows = con.execute("SELECT * FROM notes ORDER BY length(path)").fetchall()
    for r in rows:
        stem = posixpath.splitext(posixpath.basename(r["path"]))[0].lower()
        if stem == low or low in [a.lower() for a in json.loads(r["aliases"] or "[]")]:
            return r
    for r in rows:
        if low in r["path"].lower() or low in (r["title"] or "").lower():
            return r
    return None


# Isolation is measured against the complete index, never the active visual layers.
_ISOLATED = """NOT EXISTS (
    SELECT 1 FROM links l WHERE (l.src=n.id OR l.dst=n.id) AND
    ((l.dst IS NOT NULL AND l.dst!=l.src) OR
     (l.src=n.id AND l.dst IS NULL AND l.kind='wikilink' AND length(l.target)>0))
) AND NOT EXISTS (SELECT 1 FROM tags t WHERE t.note_id=n.id)"""


def isolated_paths(con) -> set[str]:
    return {r[0] for r in con.execute(f"SELECT n.path FROM notes n WHERE {_ISOLATED}")}


def isolated_report(con, pasta: str = "", limite: int = 50, offset: int = 0) -> dict:
    """Indexed files with no links, incoming links, similarity, tags or pending wikilinks."""
    prefix = pasta.strip("/")
    prefix = prefix + "/" if prefix else ""
    where = _ISOLATED + " AND substr(n.path,1,length(?))=?"
    total = con.execute(f"SELECT count(*) FROM notes n WHERE {where}", (prefix, prefix)).fetchone()[0]
    limit, offset = max(1, min(int(limite), 100)), max(0, int(offset))
    rows = con.execute(f"SELECT n.path,n.title,n.kind FROM notes n WHERE {where} ORDER BY n.path LIMIT ? OFFSET ?",
                       (prefix, prefix, limit, offset)).fetchall()
    items = []
    for r in rows:
        suggestion = ("Revisar se o arquivo faz parte de um fluxo do projeto e documentar seus vínculos; ausência de conexões no índice não prova código sem uso."
                      if r['kind'] == 'codigo' else
                      "Adicionar uma tag de assunto ou um link para uma nota relacionada, após revisar o conteúdo.")
        items.append(dict(path=r['path'], title=r['title'], kind=r['kind'], sugestao=suggestion))
    return dict(total=total, offset=offset, limite=limit, mais=offset+len(items)<total, itens=items,
                criterio="Sem links, backlinks, similaridade, tags ou wikilinks pendentes no índice completo; não significa arquivo sem uso.")


def build_graph(con, similar: bool = True, tags: bool = False, ghosts: bool = True, origem: str = "", relacao: str = "") -> nx.Graph:
    """Projeção sem direção para comunidades e desenho. Para consultas use build_relations."""
    G = nx.Graph()
    isolated = isolated_paths(con)
    for n in con.execute("SELECT id, path, title, kind, frontmatter FROM notes"):
        tipo = json.loads(n["frontmatter"] or "{}").get("tipo")
        G.add_node(n["id"], label=n["title"], path=n["path"], kind=n["kind"], tipo=tipo if isinstance(tipo, str) else None, isolated=n["path"] in isolated)

    def add(a, b, kind, w, conf=None):
        if G.has_edge(a, b):
            e = G[a][b]
            if KIND_PRIORITY[kind] > KIND_PRIORITY[e["kind"]]:
                e["kind"] = kind
                e["conf"] = conf
                e["src"] = a
            e["weight"] = max(e["weight"], w)
        else:
            G.add_edge(a, b, kind=kind, weight=w, conf=conf, src=a)

    for l in con.execute("SELECT src, target, dst, kind, weight, conf FROM links"):
        if (origem and l["conf"] != origem) or (relacao and l["kind"] != relacao):
            continue
        if l["kind"] == "similar" and not similar:
            continue
        if l["dst"] is not None:
            if l["src"] in G and l["dst"] in G:
                w = l["weight"] if l["kind"] in ("similar", "chama", "menciona") else 1.0
                add(l["src"], l["dst"], l["kind"], w or 0.5, l["conf"])
        elif ghosts and l["kind"] == "wikilink":
            gid = "ghost:" + l["target"].lower()
            if gid not in G:
                G.add_node(gid, label=l["target"], path=None, kind="fantasma")
            add(l["src"], gid, "wikilink", 1.0)
    if tags and not origem and relacao in ("", "tag"):
        for t in con.execute("SELECT note_id, tag FROM tags"):
            tid = "tag:" + t["tag"]
            if tid not in G:
                G.add_node(tid, label="#" + t["tag"], path=None, kind="tag")
            add(t["note_id"], tid, "tag", 0.5)
    return G


def build_relations(con, similar: bool = False) -> nx.MultiDiGraph:
    """Cada relação e sentido permanece separado; a projeção visual não é fonte de verdade."""
    G = nx.MultiDiGraph()
    for n in con.execute("SELECT id, path, title, kind FROM notes"):
        G.add_node(n["id"], path=n["path"], label=n["title"], kind=n["kind"], linha=None)
    for l in con.execute("SELECT rowid, src, dst, kind, weight, conf, detalhe FROM links WHERE dst IS NOT NULL"):
        if l["src"] not in G or l["dst"] not in G or (l["kind"] == "similar" and not similar):
            continue
        attrs = dict(kind=l["kind"], weight=l["weight"] or 1, conf=l["conf"] or "EXTRACTED",
                     detalhe=l["detalhe"], linha=None)
        G.add_edge(l["src"], l["dst"], key=l["rowid"], **attrs)
        if l["kind"] == "similar":
            G.add_edge(l["dst"], l["src"], key=l["rowid"], **attrs)
    return G


def communities(G: nx.Graph) -> dict:
    if G.number_of_edges() == 0:
        return {n: i for i, n in enumerate(G.nodes)}
    comms = nx.community.louvain_communities(G, weight="weight", seed=42, resolution=1.0)
    comms = sorted(comms, key=len, reverse=True)
    return {n: i for i, c in enumerate(comms) for n in c}


def hubs(G: nx.Graph, n: int = 15) -> list:
    real = [x for x in G.nodes if isinstance(x, int)]
    return sorted(real, key=lambda x: G.degree(x, weight="weight"), reverse=True)[:n]


def shortest_path(G: nx.Graph, a, b) -> list | None:
    try:
        return nx.shortest_path(G, a, b)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def neighbors(con, note_id: int) -> dict:
    out = {"links": [], "backlinks": [], "similares": [], "fantasmas": [], "tags": []}
    seen = set()
    for r in con.execute(
        "SELECT l.dst, l.target, l.kind, l.conf, l.detalhe, n.path, n.title FROM links l LEFT JOIN notes n ON n.id=l.dst "
        "WHERE l.src=? AND l.kind!='similar' ORDER BY l.kind", (note_id,)
    ):
        if r["dst"] is None:
            if r["kind"] == "wikilink":
                out["fantasmas"].append(r["target"])
        elif (r["path"], r["kind"]) not in seen:
            seen.add((r["path"], r["kind"]))
            out["links"].append({"path": r["path"], "title": r["title"], "kind": r["kind"], "conf": r["conf"], "detalhe": r["detalhe"]})
    seen = set()
    for r in con.execute(
        "SELECT l.kind, l.conf, l.detalhe, n.path, n.title FROM links l JOIN notes n ON n.id=l.src "
        "WHERE l.dst=? AND l.kind!='similar' ORDER BY l.kind", (note_id,)
    ):
        if (r["path"], r["kind"]) not in seen:
            seen.add((r["path"], r["kind"]))
            out["backlinks"].append({"path": r["path"], "title": r["title"], "kind": r["kind"], "conf": r["conf"], "detalhe": r["detalhe"]})
    for r in con.execute(
        "SELECT n.path, n.title, l.weight FROM links l JOIN notes n ON n.id = CASE WHEN l.src=? THEN l.dst ELSE l.src END "
        "WHERE l.kind='similar' AND (l.src=? OR l.dst=?) ORDER BY l.weight DESC", (note_id, note_id, note_id)
    ):
        out["similares"].append({"path": r["path"], "title": r["title"], "score": r["weight"]})
    out["tags"] = [r[0] for r in con.execute("SELECT tag FROM tags WHERE note_id=?", (note_id,))]
    return out


def vista(G: nx.Graph, prefixo: str = "", limite: int = 500, max_arestas: int = 2500,
          pagina: int = 0, foco: str = "") -> dict:
    """Exibe arquivos reais em páginas limitadas e distribuídas entre as áreas do projeto."""
    prefixo = prefixo.strip("/")
    limite = max(1, int(limite))
    pre = prefixo + "/" if prefixo else ""
    total = sum(1 for _, d in G.nodes(data=True) if d.get("path"))
    if not prefixo and G.number_of_nodes() <= limite:
        V = G
        pagina, paginas, itens = 0, 1, total
        inicio, fim = (1, total) if total else (0, 0)
    else:
        # Dentro de cada área, decisões e hubs aparecem primeiro. A intercalação
        # ponderada representa também áreas pequenas na primeira página, sem
        # transformar pastas em nós nem perder arquivos nas páginas seguintes.
        buckets = {}
        for n, d in G.nodes(data=True):
            p = d.get("path")
            if not p or not p.startswith(pre):
                continue
            bucket = p[len(pre):].split("/", 1)[0] if "/" in p[len(pre):] else ""
            buckets.setdefault(bucket, []).append(n)
        for nodes in buckets.values():
            nodes.sort(key=lambda n: (G.nodes[n].get("tipo") not in ("decisao", "aprendizado"),
                                      -G.degree(n, weight="weight"), G.nodes[n]["path"]))
        queue = [(0.0, name, 0) for name in buckets]
        heapq.heapify(queue)
        ordered = []
        while queue:
            finish, name, offset = heapq.heappop(queue)
            ordered.append(buckets[name][offset])
            if offset + 1 < len(buckets[name]):
                heapq.heappush(queue, (finish + 1 / math.sqrt(len(buckets[name])), name, offset + 1))
        itens = len(ordered)
        paginas = max(1, (itens + limite - 1) // limite)
        if foco:
            focused = next((i for i, n in enumerate(ordered) if G.nodes[n]["path"] == foco), None)
            if focused is not None:
                pagina = focused // limite
        pagina = min(max(0, int(pagina)), paginas - 1)
        inicio = pagina * limite + 1 if itens else 0
        fim = min(itens, (pagina + 1) * limite)
        V = G.subgraph(ordered[pagina * limite:fim])
    arestas = sorted(V.edges(data=True), key=lambda e: -e[2]["weight"])[:max_arestas]
    comm = communities(V)
    ids = {n: i for i, n in enumerate(V.nodes)}
    maxw = max((e[2]["weight"] for e in arestas), default=1) or 1
    return {
        "modo": "arquivos", "prefixo": prefixo, "total": total,
        "limite": limite, "pagina": pagina, "paginas": paginas, "itens": itens, "inicio": inicio, "fim": fim,
        "visiveis": V.number_of_nodes(),
        "ocultos": max(0, V.number_of_edges() - len(arestas)),
        "nodes": [{"i": ids[n], "id": str(n), "label": d["label"], "path": d.get("path"), "kind": d["kind"],
                   "isolated": d.get("isolated", False), "tipo": d.get("tipo"), "c": comm.get(n, 0), "deg": V.degree(n)}
                  for n, d in V.nodes(data=True)],
        "edges": [[ids[a], ids[b], d["kind"], round(d["weight"], 3), d.get("conf")]
                  for a, b, d in arestas],
    }
