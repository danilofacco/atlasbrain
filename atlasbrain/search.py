"""Busca híbrida: palavra-chave (FTS5/BM25) + significado (embeddings), fundidas por RRF."""

import json
import re
import threading

import numpy as np

from . import embed
from .db import from_blob, get_meta

STOPWORDS = set(
    "a o as os de da do das dos e é em no na nos nas um uma uns umas para pra por com sem que "
    "se ao aos à às ou mais como mas meu minha seu sua isso isto esse essa este esta the of and "
    "to in is it for on with as by an be are was this that or at from".split()
)

# Relative weights for the three candidate sources and implementation-oriented questions.
FTS_WEIGHT = 1.0
VEC_WEIGHT = 1.25
STRUCT_WEIGHT = 1.0
CODE_INTENT_BOOST = 1.8
CODE_RERANK_WEIGHT = .04


def _rerank_top_three(results: list[dict], note_ids: np.ndarray, similarities: np.ndarray) -> None:
    """Reorder the visible shortlist without losing candidates or overriding exact matches."""
    first = results[:3]
    for entry in first:
        scores = similarities[note_ids == entry['id']]
        if len(scores):
            entry['score'] += CODE_RERANK_WEIGHT * max(0.0, float(scores.max()))
    results[:3] = sorted(first, key=lambda e: (e['exato'], e['score']), reverse=True)


_SECUNDARIO = re.compile(r"(^|/)(__tests__|tests?|spec|__mocks__|fixtures|docs|scripts|pesquisas|examples?)/"
                         r"|\.(test|spec)\.[jt]sx?$|_test\.(py|go)$|(^|/)test_[^/]*\.py$")


_FILTRO = re.compile(r'(?:^|\s)(pasta|path|folder|tipo|type|tag|desde|since|status):("[^"]+"|\S+)', re.I)
_EXCLUI = re.compile(r'(?:^|\s)-(\w[\w-]*)')
_FRASE = re.compile(r'"([^"]{3,})"')


def parse_filtros(q: str) -> tuple[str, dict]:
    """Tira da consulta os filtros (pasta:, tipo:, tag:, desde:, -excluir, "frase exata") e devolve o resto."""
    f: dict = {}
    for k, v in _FILTRO.findall(q):
        key = {"path":"pasta", "folder":"pasta", "type":"tipo", "since":"desde"}.get(k.lower(), k.lower())
        value = v.strip('"')
        if key == 'tipo':
            value = {'code':'codigo', 'note':'nota', 'document':'documento', 'decision':'decisao', 'learning':'aprendizado'}.get(value.lower(), value)
        elif key == 'status':
            value = {'active':'ativa', 'superseded':'substituída', 'revoked':'revogada', 'cancelled':'cancelada'}.get(value.lower(), value)
        f[key] = value
    q = _FILTRO.sub(" ", q)
    f["excluir"] = [w.lower() for w in _EXCLUI.findall(q)]
    q = _EXCLUI.sub(" ", q)
    f["frases"] = [x.lower() for x in _FRASE.findall(q)]
    q = q.replace('"', " ")
    return " ".join(q.split()), {k: v for k, v in f.items() if v}


def _fts_query(q: str) -> str | None:
    tokens = [t for t in re.findall(r"\w+", q.lower()) if t not in STOPWORDS and len(t) > 1]
    if not tokens:
        return None
    # prefixo* para pegar variações (reunião → reuniões)
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in tokens)


def _filter_type(value: str) -> str:
    value = value.lower()
    aliases = {"decisão": "decisao", "decisões": "decisao", "decisoes": "decisao",
               "decisoe": "decisao", "código": "codigo", "códigos": "codigo"}
    return aliases.get(value, value.removesuffix("s"))


def _candidate_filter_sql(tag, folder, filters):
    """One parameterized eligibility query shared by all candidate sources.

    Scope is applied before each source's candidate limit, including text-level
    phrase/exclusion constraints. Unicode casing matches the Python filters.
    """
    clauses, params = [], []
    prefix = folder.lower().strip("/") + "/" if folder and folder.strip("/") else ""
    if prefix:
        clauses.append("instr(atlasbrain_lower(n.path), ?) = 1")
        params.append(prefix)
    elif folder:
        clauses.append("1=1")  # pasta:/ explicitly selects the project root
    if tag:
        clauses.append("EXISTS (SELECT 1 FROM tags t WHERE t.note_id=n.id AND t.tag=?)")
        params.append(tag.lstrip("#").lower())
    if filters.get("tipo"):
        clauses.append("(n.kind=? OR json_extract(n.frontmatter, '$.tipo')=?)")
        tipo = _filter_type(filters["tipo"])
        params.extend((tipo, tipo))
    if filters.get("status"):
        clauses.append("atlasbrain_casefold(COALESCE(NULLIF(json_extract(n.frontmatter, '$.status'), ''), 'ativa'))=?")
        params.append(filters["status"].casefold())
    if filters.get("desde"):
        clauses.append("CAST(COALESCE(NULLIF(json_extract(n.frontmatter, '$.data'), ''), "
                       "date(COALESCE(n.mtime, 0), 'unixepoch', 'localtime')) AS TEXT)>=?")
        params.append(filters["desde"])
    text = "atlasbrain_lower(COALESCE(n.title, '') || ' ' || COALESCE(c.text, ''))"
    for word in filters.get("excluir", ()):
        clauses.append(f"instr({text}, ?) = 0")
        params.append(word)
    for phrase in filters.get("frases", ()):
        clauses.append(f"instr({text}, ?) > 0")
        params.append(phrase)
    return " AND ".join(clauses), params


def _procura_implementacao(q: str) -> bool:
    """Perguntas sobre execução/localização tendem a pedir o arquivo que implementa a resposta."""
    text = q.casefold().strip()
    if re.search(r'\b(decidimos|decisão tomada|foi decidid[ao]|foi escolhida|aprendemos|por que escolhemos|'
                 r'registrad[ao].{0,20}decis\w*|nota.{0,12}(?:decisão|aprendizado))\b', text):
        return False
    return bool(re.match(r'(onde|where|como|how|qual (?:arquivo|função|módulo|tela))\b', text)
                or re.match(r'(detectar|validar|converter|executar|classificar|descobrir|agrupar|injetar|transformar)\b', text))


class Searcher:
    def __init__(self, con):
        self.con = con
        # SQLite's built-in lower() handles ASCII only. Keep accented paths and
        # phrase filters consistent with Python's Unicode matching.
        con.create_function("atlasbrain_lower", 1, lambda value: str(value or "").lower(), deterministic=True)
        con.create_function("atlasbrain_casefold", 1, lambda value: str(value or "").casefold(), deterministic=True)
        self.lock = threading.Lock()
        self._rev = None
        self._ids = None
        self._notes = None
        self._M = None
        self._coverage_rev = object()
        self._coverage = ({}, set())
        self._code_vector_rev = object()
        self._code_vectors_complete = False

    def _has_complete_code_vectors(self) -> bool:
        """Refine only small projects whose code bodies are fully embedded."""
        rev = get_meta(self.con, "rev")
        if rev != self._code_vector_rev:
            total, embedded = self.con.execute(
                "SELECT COUNT(*), COUNT(c.embedding) FROM chunks c "
                "JOIN notes n ON n.id=c.note_id WHERE n.kind='codigo' AND c.ord>=0"
            ).fetchone()
            self._code_vectors_complete = 2 <= total <= 1000 and embedded == total
            self._code_vector_rev = rev
        return self._code_vectors_complete

    def _load_vectors(self):
        rev = get_meta(self.con, "rev")
        if rev == self._rev and self._M is not None:
            return
        rows = self.con.execute("SELECT id, note_id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
        if rows:
            self._ids = np.array([r[0] for r in rows])
            self._notes = np.array([r[1] for r in rows])
            self._M = np.stack([from_blob(r[2]) for r in rows])
        else:
            self._ids = self._notes = self._M = None
        self._rev = rev

    def search(self, q: str, limit: int = 10, tag: str | None = None, folder: str | None = None,
               sinais: tuple = ("fts", "vec", "struct", "demote", "exact", "status"),
               rerank: bool = True) -> list[dict]:
        """`sinais` liga/desliga cada fonte (palavra, significado, símbolo/caminho): o benchmark mede cada uma.
        A consulta aceita filtros: pasta:lib/ai tipo:decisao tag:x desde:2026-09 -excluir "frase exata"."""
        q, filtros = parse_filtros(q)
        tag = tag or filtros.get("tag")
        folder = folder or filtros.get("pasta")
        if not q and filtros.get("frases"):
            q = " ".join(filtros["frases"])
        with self.lock:
            return self._search(q, limit, tag, folder, set(sinais), filtros, rerank)

    def _search(self, q, limit, tag, folder, sinais, filtros=None, rerank=True):
        filtros = filtros or {}
        code_intent = (not filtros.get('tipo') and _procura_implementacao(q)
                       and self.con.execute("SELECT 1 FROM notes WHERE kind='codigo' LIMIT 1").fetchone() is not None)
        pool = 200
        where, scope_params = _candidate_filter_sql(tag, folder, filtros)
        scope_rows = self.con.execute(
            "SELECT c.id, c.note_id FROM notes n CROSS JOIN chunks c ON c.note_id=n.id WHERE " + where,
            scope_params).fetchall() if where else None
        eligible = {r[0] for r in scope_rows} if scope_rows is not None else None
        eligible_notes = {r[1] for r in scope_rows} if scope_rows is not None else None
        if eligible is not None and not eligible:
            return []
        # Materialize eligibility once. Re-running the full scope subquery for
        # every symbol/path candidate can multiply work on large repositories.
        scope_args = [json.dumps(sorted(eligible))] if eligible is not None else []
        chunk_scope = " AND c.id IN (SELECT value FROM json_each(?))" if eligible is not None else ""

        def first_chunk(note_id):
            if eligible_notes is not None and note_id not in eligible_notes:
                return None
            rows = self.con.execute("SELECT id FROM chunks WHERE note_id=? ORDER BY ord", (note_id,))
            return next((row for row in rows if eligible is None or row[0] in eligible), None)

        rrf: dict[int, float] = {}
        snippets: dict[int, str] = {}
        sources = {}
        exact_notes = set()
        target = q.strip().strip('`').casefold()
        if target and 'exact' in sinais:
            for row in self.con.execute('SELECT id, path, title FROM notes'):
                if target in (row['path'].casefold(), row['path'].split('/')[-1].casefold(), row['title'].casefold()):
                    exact_notes.add(row['id'])
            for row in self.con.execute('SELECT DISTINCT note_id FROM simbolos WHERE lower(nome)=?', (target,)):
                exact_notes.add(row[0])
            for note_id in exact_notes:
                row = first_chunk(note_id)
                if row:
                    rrf[row[0]] = .1
                    sources.setdefault(row[0], set()).add('correspondência exata')

        match = _fts_query(q) if "fts" in sinais else None
        if match:
            try:
                if eligible is None:
                    rows = self.con.execute(
                        "SELECT rowid, snippet(chunks_fts, 2, '**', '**', ' … ', 24) AS snip "
                        "FROM chunks_fts WHERE chunks_fts MATCH ? "
                        "ORDER BY bm25(chunks_fts, 8.0, 3.0, 1.0) LIMIT ?", (match, pool)).fetchall()
                    ranked = [(rank, row['rowid']) for rank, row in enumerate(rows)]
                else:
                    # Preserve each source's global rank while filling a scoped
                    # shortlist. Rebasing ranks changed proven result ordering.
                    ranked = []
                    for rank, row in enumerate(self.con.execute(
                            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                            "ORDER BY bm25(chunks_fts, 8.0, 3.0, 1.0)", (match,))):
                        if row[0] in eligible:
                            ranked.append((rank, row[0]))
                            if len(ranked) == pool:
                                break
                    # Unary + prevents FTS5 from restarting MATCH for every
                    # rowid. Generate snippets only for the selected passages.
                    rows = self.con.execute(
                        "SELECT rowid, snippet(chunks_fts, 2, '**', '**', ' … ', 24) AS snip "
                        "FROM chunks_fts WHERE chunks_fts MATCH ? "
                        "AND +rowid IN (SELECT value FROM json_each(?))",
                        (match, json.dumps([cid for _, cid in ranked]))).fetchall() if ranked else []
            except Exception:
                rows, ranked = [], []
            for row in rows:
                snippets[row['rowid']] = row['snip']
            for rank, cid in ranked:
                rrf[cid] = rrf.get(cid, 0) + FTS_WEIGHT / (60 + rank)
                sources.setdefault(cid, set()).add('palavras-chave')

        # 3º sinal: nome de símbolo ou de arquivo bate com a consulta
        struct = []
        idents = [t for t in re.findall(r"[A-Za-z_][\w]{2,}", q) if t.lower() not in STOPWORDS]
        if idents and "struct" in sinais:
            marks = ",".join("?" * len(idents))
            for r in self.con.execute(
                f"SELECT s.note_id, s.linha, (SELECT c.id FROM chunks c WHERE c.note_id=s.note_id AND c.heading LIKE '%'||s.nome||'%' "
                + chunk_scope +
                f" ORDER BY c.ord LIMIT 1) cid FROM simbolos s WHERE s.nome IN ({marks}) COLLATE NOCASE "
                + ("AND cid IS NOT NULL " if eligible is not None else "") + "LIMIT 30", [*scope_args, *idents]):
                if r["cid"]:
                    struct.append(r["cid"])
            # caminho: só quando a palavra É o nome de um arquivo ou pasta (registro → registro.py). Trecho de
            # nome casava palavra comum ("projeto") com qualquer nota que a tivesse no título — medido no bench.
            alvo = {t.lower() for t in idents if len(t) >= 4}
            if alvo:
                for r in self.con.execute("SELECT id, path FROM notes WHERE kind='codigo' OR kind='documento'"):
                    partes = r["path"].lower().split("/")
                    nomes = {re.sub(r"\.[^.]+$", "", partes[-1]), *partes[:-1]}
                    if alvo & nomes:
                        c = first_chunk(r["id"])
                        if c:
                            struct.append(c[0])
        for rank, cid in enumerate(dict.fromkeys(struct)):
            rrf[cid] = rrf.get(cid, 0) + STRUCT_WEIGHT / (60 + rank)
            sources.setdefault(cid, set()).add('símbolo ou caminho')

        semantic_scores = None
        if q and embed.enabled() and "vec" in sinais:
            self._load_vectors()
            if self._M is not None:
                weight = VEC_WEIGHT if code_intent else 1.0
                qv = embed.embed([q])[0]
                scores = self._M @ qv
                semantic_scores = scores
                order = np.argsort(-scores)
                if eligible is None:
                    top = list(enumerate(order[:pool]))
                else:
                    positions = np.flatnonzero(np.isin(self._ids[order], np.fromiter(eligible, dtype=np.int64)))[:pool]
                    top = [(int(rank), order[rank]) for rank in positions]
                for rank, i in top:
                    if scores[i] < 0.2:
                        break
                    cid = int(self._ids[i])
                    rrf[cid] = rrf.get(cid, 0) + weight / (60 + rank)
                    sources.setdefault(cid, set()).add('semelhança semântica')

        if not q and where:
            # A filters-only query is a bounded listing, not an embedding of an
            # empty string. Pick one eligible passage per file, newest first.
            rows = self.con.execute(
                "SELECT MIN(c.id) FROM chunks c JOIN notes n ON n.id=c.note_id WHERE " + where +
                " GROUP BY n.id ORDER BY n.mtime DESC, n.path LIMIT ?", [*scope_params, pool])
            for row in rows:
                rrf[row[0]] = .01
                sources.setdefault(row[0], set()).add('filtros da consulta')
        if not rrf:
            return []
        cids = sorted(rrf, key=rrf.get, reverse=True)[: pool * 2]
        marks = ",".join("?" * len(cids))
        info = {
            r["id"]: r
            for r in self.con.execute(
                f"SELECT c.id, c.note_id, c.heading, c.text, n.path, n.title, n.kind, n.mtime, n.size, "
                f"json_extract(n.frontmatter, '$.status') status_fm, json_extract(n.frontmatter, '$.tipo') tipo_fm, json_extract(n.frontmatter, '$.data') data_fm FROM chunks c "
                f"JOIN notes n ON n.id=c.note_id WHERE c.id IN ({marks})",
                cids,
            )
        }
        tokens = [t for t in re.findall(r"\w+", q.lower()) if t not in STOPWORDS]
        by_note: dict[int, dict] = {}
        for cid in cids:
            c = info.get(cid)
            if not c:
                continue
            entry = by_note.get(c["note_id"])
            if entry is None:
                title_bonus = 0.01 if tokens and all(t in (c["title"] or "").lower() for t in tokens) else 0
                snippet = snippets.get(cid) or " ".join(c["text"].split())[:300]
                by_note[c["note_id"]] = {
                    "id": c["note_id"], "tokens": max(1, (c["size"] or 0) // 4),
                    "path": c["path"], "title": c["title"], "kind": c["kind"],
                    "heading": c["heading"], "snippet": snippet,
                    "score": rrf[cid] + title_bonus, "hits": 1,
                    "motivos": sorted(sources.get(cid, set())),
                    "status": c['status_fm'] or '', "exato": c['note_id'] in exact_notes,
                }
            else:
                if entry['hits'] == 1:
                    entry['score'] += .25 * rrf[cid]
                entry["hits"] += 1
        for e in by_note.values():
            if code_intent and e['kind'] == 'codigo' and not e['exato']:
                e['score'] *= CODE_INTENT_BOOST
                e['motivos'].append('consulta sobre implementação')
        if "demote" in sinais:  # testes, docs e scripts competem com a implementação: pesam menos
            for e in by_note.values():
                if not e["exato"] and _SECUNDARIO.search(e["path"]):
                    e["score"] *= 0.55
        for e in by_note.values():
            if e['status'] in ('substituída', 'substituida', 'revogada', 'cancelada'):
                e['motivos'].append('decisão histórica: ' + e['status'])
                if 'status' in sinais and not filtros.get('status'):
                    e['score'] *= .35
        from .consolidacao import coverage
        coverage_rev = get_meta(self.con, 'rev')
        if coverage_rev != self._coverage_rev:
            self._coverage = coverage(self.con)
            self._coverage_rev = coverage_rev
        covered, stale = self._coverage
        summary_paths = {e['path'] for e in by_note.values()} - stale
        for e in by_note.values():
            if e['path'] in stale:
                e['motivos'].append('síntese desatualizada: consulte as fontes')
                if not e['exato']:
                    e['score'] *= .35
            elif not e['exato'] and covered.get(e['path']) in summary_paths:
                e['motivos'].append('fonte disponível na síntese: '+covered[e['path']])
                e['score'] *= .65
        results = (sorted(by_note.values(), key=lambda e: (e['exato'], e['score']), reverse=True)
                   if q else list(by_note.values()))
        if (rerank and code_intent and semantic_scores is not None
                and len(results) >= 2 and sum(e['kind'] == 'codigo' for e in results[:3]) >= 2
                and self._has_complete_code_vectors()):
            _rerank_top_three(results, self._notes, semantic_scores)
        results = results[:limit]
        for r in results:
            r["score"] = round(r["score"] * 1000, 1)
        return results
