"""Consultas estruturais puras (sem servidor): usadas pelo MCP, pelo benchmark e pelos testes."""

import re


def achar_arquivos(con, consulta: str, limite: int = 20) -> list[dict]:
    """Arquivos cujo caminho ou símbolos batem com a consulta, do mais provável ao menos."""
    toks = [t for t in re.findall(r"[\w-]+", consulta.lower()) if len(t) > 1]
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
    return [{"path": n["path"], "kind": n["kind"], "simbolos": sy, "score": sc} for sc, n, sy in scored[:limite]]


_CAB = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def secoes_markdown(texto: str) -> list[tuple[str, int, int, int]]:
    """(título, nível, linha início, linha fim) de cada cabeçalho, fim exclusivo."""
    linhas = texto.splitlines()
    heads, cerca = [], False
    for i, l in enumerate(linhas):
        if l.lstrip().startswith(("```", "~~~")):
            cerca = not cerca
        m = None if cerca else _CAB.match(l)
        if m:
            heads.append((m.group(2).strip(), len(m.group(1)), i))
    out = []
    for j, (t, nivel, ini) in enumerate(heads):
        fim = next((h[2] for h in heads[j + 1:] if h[1] <= nivel), len(linhas))
        out.append((t, nivel, ini, fim))
    return out


def extrair_secao(con, row, texto: str, secao: str) -> tuple[str | None, list[str]]:
    """Só o pedaço pedido: um cabeçalho de nota/documento ou um símbolo (função/classe) de código.
    Devolve (trecho ou None, nomes disponíveis para o agente escolher)."""
    alvo = secao.strip().lower().lstrip("#").strip()
    linhas = texto.splitlines()
    if row["kind"] == "codigo":
        sims = con.execute("SELECT nome, linha, pai FROM simbolos WHERE note_id=? ORDER BY linha", (row["id"],)).fetchall()
        nomes = [s["nome"] if not s["pai"] else f"{s['pai']}.{s['nome']}" for s in sims]
        for s, nome in zip(sims, nomes):
            if alvo in (s["nome"].lower(), nome.lower()):
                ini = s["linha"] - 1
                # começa no comentário/decorador logo acima
                while ini > 0 and linhas[ini - 1].strip().startswith(("#", "//", "*", "/*", "@")):
                    ini -= 1
                prox = [x["linha"] for x in sims if x["linha"] > s["linha"] and (x["pai"] == s["pai"] or not x["pai"])]
                fim = (prox[0] - 1) if prox else len(linhas)
                return "\n".join(f"{n + 1:>5}  {l}" for n, l in enumerate(linhas[ini:fim], start=ini)), nomes
        return None, nomes
    secs = secoes_markdown(texto)
    for t, _, ini, fim in secs:
        if alvo == t.lower() or alvo in t.lower():
            return "\n".join(linhas[ini:fim]).strip(), [x[0] for x in secs]
    return None, [x[0] for x in secs]


def conexoes(con, note_id: int, limite: int = 12) -> list[dict]:
    """Smart Connections: o que mais se parece em significado com a nota aberta, com o trecho que casou.
    Usa o vetor da nota contra os vetores de todos os trechos (não depende do limiar do grafo)."""
    import numpy as np
    from .db import from_blob

    row = con.execute("SELECT embedding FROM notes WHERE id=?", (note_id,)).fetchone()
    if not row or row[0] is None:
        return []
    alvo = from_blob(row[0])
    rows = con.execute("SELECT c.id, c.note_id, c.heading, c.text, c.embedding FROM chunks c "
                       "WHERE c.embedding IS NOT NULL AND c.note_id != ?", (note_id,)).fetchall()
    if not rows:
        return []
    M = np.stack([from_blob(r["embedding"]) for r in rows])
    sc = M @ alvo
    melhor: dict[int, tuple[float, int]] = {}
    for i in np.argsort(-sc)[: limite * 8]:
        nid = rows[i]["note_id"]
        if nid not in melhor:
            melhor[nid] = (float(sc[i]), int(i))
        if len(melhor) >= limite:
            break
    info = {r["id"]: r for r in con.execute(
        f"SELECT id, path, title, kind FROM notes WHERE id IN ({','.join('?' * len(melhor))})", list(melhor))}
    out = []
    for nid, (score, i) in sorted(melhor.items(), key=lambda kv: -kv[1][0]):
        r = rows[i]
        trecho = " ".join(r["text"].split())[:220]
        out.append({"id": nid, "path": info[nid]["path"], "title": info[nid]["title"], "kind": info[nid]["kind"],
                    "score": round(score, 3), "heading": r["heading"], "trecho": trecho})
    return out


CODE_KINDS_DEP = ("importa", "chama", "herda")


def dependencias(con, note_id: int, profundidade: int = 2, max_por_lado: int = 40) -> dict:
    """Mapa de um arquivo: o que ele usa (lado +1, +2) e quem usa ele (lado -1, -2), só relações de código."""
    kinds = ",".join("?" * len(CODE_KINDS_DEP))
    saida, entrada = {}, {}
    for r in con.execute(f"SELECT src, dst, kind, conf, detalhe FROM links WHERE dst IS NOT NULL AND kind IN ({kinds})",
                         CODE_KINDS_DEP):
        saida.setdefault(r["src"], []).append(r)
        entrada.setdefault(r["dst"], []).append(r)
    nos = {note_id: 0}
    arestas = {}
    for sinal, mapa, campo in ((1, saida, "dst"), (-1, entrada, "src")):
        fronteira = [note_id]
        for nivel in range(1, profundidade + 1):
            prox = []
            for n in fronteira:
                for r in mapa.get(n, []):
                    outro = r[campo]
                    arestas[(r["src"], r["dst"], r["kind"])] = r
                    if outro not in nos and sum(1 for v in nos.values() if v == sinal * nivel) < max_por_lado:
                        nos[outro] = sinal * nivel
                        prox.append(outro)
            fronteira = prox
    info = {r["id"]: r for r in con.execute(
        f"SELECT id, path, title, kind FROM notes WHERE id IN ({','.join('?' * len(nos))})", list(nos))}
    return {
        "nodes": [{"id": n, "path": info[n]["path"], "label": info[n]["path"].rsplit("/", 1)[-1], "lado": lado}
                  for n, lado in nos.items() if n in info],
        "edges": [{"src": s, "dst": d, "kind": k, "conf": r["conf"], "detalhe": r["detalhe"]}
                  for (s, d, k), r in arestas.items() if s in nos and d in nos],
    }
