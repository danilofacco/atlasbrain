"""Consultas estruturais puras (sem servidor): usadas pelo MCP, pelo benchmark e pelos testes."""

import re


def find_files(con, query: str, limit: int = 20) -> list[dict]:
    """Arquivos cujo caminho ou símbolos batem com a consulta, do mais provável ao menos."""
    toks = [t for t in re.findall(r"[\w-]+", query.lower()) if len(t) > 1]
    if not toks:
        return []
    notes = con.execute("SELECT id, path, kind FROM notes").fetchall()
    sims: dict[int, set] = {}
    for t in toks:
        for r in con.execute("SELECT note_id, nome FROM simbolos WHERE lower(nome) LIKE ?", (f"%{t}%",)):
            sims.setdefault(r[0], set()).add(r[1])
    scored = []
    for n in notes:
        path = n["path"].lower()
        base = path.rsplit("/", 1)[-1]
        hit = sum(1 for t in toks if t in path)
        score = hit * 2 + sum(3 for t in toks if t in base) + (len(sims.get(n["id"], ())) and 2)
        if hit == len(toks):
            score += 4
        if score:
            scored.append((score, n, sorted(sims.get(n["id"], ()))[:5]))
    scored.sort(key=lambda x: (-x[0], len(x[1]["path"])))
    return [{"path": n["path"], "kind": n["kind"], "simbolos": sy, "score": sc} for sc, n, sy in scored[:limit]]


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def markdown_sections(text: str) -> list[tuple[str, int, int, int]]:
    """(título, nível, linha início, linha fim) de cada cabeçalho, fim exclusivo."""
    lines = text.splitlines()
    heads, near = [], False
    for i, l in enumerate(lines):
        if l.lstrip().startswith(("```", "~~~")):
            near = not near
        m = None if near else _HEADING.match(l)
        if m:
            heads.append((m.group(2).strip(), len(m.group(1)), i))
    out = []
    for j, (t, level, ini) in enumerate(heads):
        end = next((h[2] for h in heads[j + 1:] if h[1] <= level), len(lines))
        out.append((t, level, ini, end))
    return out


def extract_section(con, row, text: str, section: str) -> tuple[str | None, list[str]]:
    """Só o pedaço pedido: um cabeçalho de nota/documento ou um símbolo (função/classe) de código.
    Devolve (trecho ou None, nomes disponíveis para o agente escolher)."""
    target = section.strip().lower().lstrip("#").strip()
    lines = text.splitlines()
    if row["kind"] == "codigo":
        sims = con.execute("SELECT nome, linha, pai FROM simbolos WHERE note_id=? ORDER BY linha", (row["id"],)).fetchall()
        names = [s["nome"] if not s["pai"] else f"{s['pai']}.{s['nome']}" for s in sims]
        for s, name in zip(sims, names):
            if target in (s["nome"].lower(), name.lower()):
                ini = s["linha"] - 1
                # começa no comentário/decorador logo acima
                while ini > 0 and lines[ini - 1].strip().startswith(("#", "//", "*", "/*", "@")):
                    ini -= 1
                next_item = [x["linha"] for x in sims if x["linha"] > s["linha"] and (x["pai"] == s["pai"] or not x["pai"])]
                end = (next_item[0] - 1) if next_item else len(lines)
                return "\n".join(f"{n + 1:>5}  {l}" for n, l in enumerate(lines[ini:end], start=ini)), names
        return None, names
    secs = markdown_sections(text)
    for t, _, ini, end in secs:
        if target == t.lower() or target in t.lower():
            return "\n".join(lines[ini:end]).strip(), [x[0] for x in secs]
    return None, [x[0] for x in secs]


def connections(con, note_id: int, limit: int = 12) -> list[dict]:
    """Smart Connections: o que mais se parece em significado com a nota aberta, com o trecho que casou.
    Usa o vetor da nota contra os vetores de todos os trechos (não depende do limiar do grafo)."""
    import numpy as np
    from .db import from_blob

    row = con.execute("SELECT embedding FROM notes WHERE id=?", (note_id,)).fetchone()
    if not row or row[0] is None:
        return []
    target = from_blob(row[0])
    rows = con.execute("SELECT c.id, c.note_id, c.heading, c.text, c.embedding FROM chunks c "
                       "WHERE c.embedding IS NOT NULL AND c.note_id != ?", (note_id,)).fetchall()
    if not rows:
        return []
    M = np.stack([from_blob(r["embedding"]) for r in rows])
    sc = M @ target
    best: dict[int, tuple[float, int]] = {}
    for i in np.argsort(-sc)[: limit * 8]:
        nid = rows[i]["note_id"]
        if nid not in best:
            best[nid] = (float(sc[i]), int(i))
        if len(best) >= limit:
            break
    info = {r["id"]: r for r in con.execute(
        f"SELECT id, path, title, kind FROM notes WHERE id IN ({','.join('?' * len(best))})", list(best))}
    out = []
    for nid, (score, i) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        r = rows[i]
        snippet = " ".join(r["text"].split())[:220]
        out.append({"id": nid, "path": info[nid]["path"], "title": info[nid]["title"], "kind": info[nid]["kind"],
                    "score": round(score, 3), "heading": r["heading"], "trecho": snippet})
    return out


CODE_KINDS_DEP = ("importa", "chama", "herda")


def dependencies(con, note_id: int, depth: int = 2, max_per_side: int = 40) -> dict:
    """Mapa de um arquivo: o que ele usa (lado +1, +2) e quem usa ele (lado -1, -2), só relações de código."""
    kinds = ",".join("?" * len(CODE_KINDS_DEP))
    outgoing, incoming = {}, {}
    for r in con.execute(f"SELECT src, dst, kind, conf, detalhe FROM links WHERE dst IS NOT NULL AND kind IN ({kinds})",
                         CODE_KINDS_DEP):
        outgoing.setdefault(r["src"], []).append(r)
        incoming.setdefault(r["dst"], []).append(r)
    nodes = {note_id: 0}
    edges = {}
    for signal, graph_map, field in ((1, outgoing, "dst"), (-1, incoming, "src")):
        frontier = [note_id]
        for level in range(1, depth + 1):
            next_item = []
            for n in frontier:
                for r in graph_map.get(n, []):
                    other = r[field]
                    edges[(r["src"], r["dst"], r["kind"])] = r
                    if other not in nodes and sum(1 for v in nodes.values() if v == signal * level) < max_per_side:
                        nodes[other] = signal * level
                        next_item.append(other)
            frontier = next_item
    info = {r["id"]: r for r in con.execute(
        f"SELECT id, path, title, kind FROM notes WHERE id IN ({','.join('?' * len(nodes))})", list(nodes))}
    return {
        "nodes": [{"id": n, "path": info[n]["path"], "label": info[n]["path"].rsplit("/", 1)[-1], "lado": side}
                  for n, side in nodes.items() if n in info],
        "edges": [{"src": s, "dst": d, "kind": k, "conf": r["conf"], "detalhe": r["detalhe"]}
                  for (s, d, k), r in edges.items() if s in nodes and d in nodes],
    }
