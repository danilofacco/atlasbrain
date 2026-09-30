"""Indexação incremental da pasta: só reprocessa o que mudou."""

import fcntl
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import embed
from .config import (
    BRAIN, EMBED_MODEL, IGNORE_DIRS, MAX_DOC_BYTES, MAX_TEXT_BYTES, SIMILAR_K, SIMILAR_MIN,
    data_dir, kind_of,
)
from .db import connect, from_blob, get_meta, set_meta, to_blob
from . import code
from .extract import extract_text
from .parse import parse


SCHEMA_VERSION = "5"
GENERATED_FILES = {"REPORT.md", "RELATORIO.md"}
_SKIPPED: dict[str, str] = {}
AUTO_CODE_CHUNK_LIMIT = 1000


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)  # nunca stdout: o MCP usa stdout


def _ignore_patterns(vault: Path) -> list[str]:
    out = []
    for name in (".atlasbrainignore", ".cerebroignore"):
        f = vault / name
        if f.exists():
            out += [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return out


def _git_files(vault: Path) -> list[str] | None:
    """Num repositório git, a lista de arquivos vem do próprio git (versionados + novos não ignorados):
    respeita o .gitignore do projeto, então build, dist e dependências ficam de fora sozinhos."""
    if not (vault / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "-C", str(vault), "ls-files", "-co", "--exclude-standard", "-z"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [f for f in r.stdout.decode("utf-8", "replace").split("\0") if f]


def _walk(vault: Path, root: Path, patterns: list[str]):
    for dirpath, dirs, files in os.walk(root):
        rel_root = os.path.relpath(dirpath, vault)
        rel_root = "" if rel_root == "." else rel_root
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS and not d.startswith(".")
            and not any(fnmatch.fnmatch(posixpath.join(rel_root, d), p.rstrip("/")) for p in patterns)
        ]
        for name in files:
            if not name.startswith("."):
                yield posixpath.join(rel_root, name) if rel_root else name


def scan(vault: Path) -> dict[str, Path]:
    patterns = _ignore_patterns(vault)
    rels = _git_files(vault)
    if rels is None:
        rels = list(_walk(vault, vault, patterns))
    else:
        parts_ok = lambda r: not any(p in IGNORE_DIRS or p.startswith(".") for p in r.split("/")[:-1])
        rels = [r for r in rels if parts_ok(r) and not posixpath.basename(r).startswith(".")]
    brain = vault / BRAIN  # as notas do cérebro entram sempre, mesmo sendo pasta oculta
    if brain.is_dir():  # REPORT.md é gerado a cada indexação: indexá-lo faria o índice mudar sem parar
        rels += [posixpath.join(BRAIN, r) for r in _walk(brain, brain, []) if r not in GENERATED_FILES]
    found: dict[str, Path] = {}
    for rel in dict.fromkeys(rels):
        name = posixpath.basename(rel)
        if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns):
            continue
        path = vault / rel
        if kind_of(path) and path.is_file():
            found[rel] = path
    return found


class _IndexLock:
    """Garante que só um processo (MCP do Claude, do Codex, web...) indexe por vez."""

    def __init__(self, vault: Path, wait_timeout: float = 0):
        self.path = data_dir(vault) / "index.lock"
        self.fh = None
        self.wait_timeout = wait_timeout

    def __enter__(self):
        self.fh = open(self.path, "w")
        limit = time.time() + self.wait_timeout
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if time.time() >= limit:
                    return False
                time.sleep(0.5)  # comando explícito: espera o ciclo automático de outro processo terminar

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _delete_note_content(con, note_id: int) -> None:
    # índice de busca sem cópia do texto (external content): remover exige os valores antigos
    con.execute("INSERT INTO chunks_fts(chunks_fts, rowid, title, heading, text) "
                "SELECT 'delete', id, title, heading, text FROM chunks WHERE note_id=?", (note_id,))
    con.execute("DELETE FROM chunks WHERE note_id=?", (note_id,))
    con.execute("DELETE FROM links WHERE src=? AND kind IN ('wikilink','mdlink')", (note_id,))
    con.execute("DELETE FROM tags WHERE note_id=?", (note_id,))
    for t in ("simbolos", "porques", "refs"):
        con.execute(f"DELETE FROM {t} WHERE note_id=?", (note_id,))


_LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "composer.lock",
              "gemfile.lock", "uv.lock", "bun.lockb", "go.sum", "pipfile.lock"}
_GENERATED = re.compile(r"@generated|do not edit|auto-?generated|code generated by|this file was generated|"
                     r"arquivo gerado automaticamente|não edite", re.I)


def skip_reason(rel: str, text: str, kind: str) -> str | None:
    """Arquivos que só poluem busca e grafo: lockfile, minificado, gerado por ferramenta."""
    name = posixpath.basename(rel).lower()
    if name in _LOCKFILES or name.endswith((".lock", "-lock.yaml", "-lock.json")):
        return "lockfile"
    if re.search(r"\.min\.(js|css)$|\.bundle\.js$|\.map$", name):
        return "minificado"
    if kind == "codigo":
        if _GENERATED.search(text[:600]):
            return "gerado por ferramenta"
        lines = text.count("\n") + 1
        if len(text) > 5000 and len(text) / lines > 400:
            return "minificado (linhas muito longas)"
    return None


def _index_file(con, rel: str, path: Path, st, existing, force: bool = False) -> bool:
    kind = kind_of(path)
    limit = MAX_DOC_BYTES if kind == "documento" else MAX_TEXT_BYTES
    if st.st_size > limit:
        return False
    raw = path.read_bytes()
    digest = hashlib.sha1(raw).hexdigest()
    if existing and existing["hash"] == digest and not force:
        con.execute("UPDATE notes SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, existing["id"]))
        return False
    text = extract_text(path, raw)
    if text is None:
        return False
    skip = skip_reason(rel, text, kind)
    if skip:
        if existing:  # virou gerado/minificado: sai do índice
            _delete_note_content(con, existing["id"])
            con.execute("DELETE FROM notes WHERE id=?", (existing["id"],))
        _SKIPPED[rel] = [skip, st.st_mtime, st.st_size]
        return False
    p = parse(text, path, kind)
    preview = " ".join(p.body.split())[:280]
    fm_json = json.dumps(p.frontmatter, ensure_ascii=False, default=str)
    if existing:
        note_id = existing["id"]
        _delete_note_content(con, note_id)
        con.execute(
            "UPDATE notes SET title=?, kind=?, mtime=?, size=?, hash=?, frontmatter=?, aliases=?, preview=?, embedding=NULL WHERE id=?",
            (p.title, kind, st.st_mtime, st.st_size, digest, fm_json, json.dumps(p.aliases), preview, note_id),
        )
    else:
        cur = con.execute(
            "INSERT INTO notes(path, title, kind, mtime, size, hash, frontmatter, aliases, preview) VALUES(?,?,?,?,?,?,?,?,?)",
            (rel, p.title, kind, st.st_mtime, st.st_size, digest, fm_json, json.dumps(p.aliases), preview),
        )
        note_id = cur.lastrowid
    e = code.extract_code(text, path.suffix) if kind == "codigo" and code.is_supported(path.suffix) else None
    snippets = [(i, h, t) for i, (h, t) in enumerate(p.chunks)]
    if kind == "codigo":
        if e:  # cabeçalho de cada trecho ganha os símbolos definidos nele: a busca por "função X" acha
            snippets = [(i, _with_symbols(h, e), t) for i, h, t in snippets]
        # trecho-resumo: do que o arquivo trata, sem o ruído do código (a busca por conceito cai aqui)
        summary = code.file_summary(rel, text, [n for n, _, _, parent in (e.symbols if e else []) if not parent])
        snippets.insert(0, (-1, "resumo do arquivo", summary))
    for i, heading, chunk in snippets:
        cid = con.execute("INSERT INTO chunks(note_id, ord, heading, text, title) VALUES(?,?,?,?,?)",
                          (note_id, i, heading, chunk, p.title)).lastrowid
        con.execute("INSERT INTO chunks_fts(rowid, title, heading, text) VALUES(?,?,?,?)", (cid, p.title, heading, chunk))
    con.executemany("INSERT INTO links(src, target, kind) VALUES(?,?,?)", [(note_id, t, k) for t, k in p.links])
    con.executemany("INSERT INTO tags(note_id, tag) VALUES(?,?)", [(note_id, t) for t in sorted(p.tags)])
    if e:
        con.executemany("INSERT INTO simbolos(note_id, nome, tipo, linha, pai) VALUES(?,?,?,?,?)",
                        [(note_id, *x) for x in e.symbols])
        con.executemany("INSERT INTO porques(note_id, etiqueta, texto, linha) VALUES(?,?,?,?)",
                        [(note_id, *x) for x in e.rationales])
        con.executemany("INSERT INTO refs(note_id, tipo, alvo, linha, quem) VALUES(?,?,?,?,?)",
                        [(note_id, "import", m, l, None) for m, l in e.imports]
                        + [(note_id, "call", n, l, q) for q, n, l in e.calls]
                        + [(note_id, "inherits", b, l, c) for c, b, l in e.inheritances]
                        + [(note_id, "binding", json.dumps([m, original]), l, local)
                           for local, m, original, l in e.bindings]
                        + [(note_id, "export", symbol, l, name) for name, symbol, l in e.exports]
                        + [(note_id, "shadow", name, l, scope) for scope, name, l in e.shadows])
    elif kind in ("nota", "documento"):  # decisões/ADRs que citam arquivos viram arestas até o código
        mentions = {m.group(1) for m in code.MENTION_RE.finditer(p.body) if "://" not in m.group(0)}
        con.executemany("INSERT INTO refs(note_id, tipo, alvo, linha, quem) VALUES(?,?,?,?,?)",
                        [(note_id, "menciona", m, None, None) for m in sorted(mentions) if m != rel])
    return True


def _with_symbols(heading: str, e) -> str:
    m = re.search(r"linhas (\d+)-(\d+)", heading or "")
    if not m:
        return heading
    a, b = int(m.group(1)), int(m.group(2))
    names = [n for n, _, l, _ in e.symbols if a <= l <= b][:8]
    return f"{heading} · {', '.join(names)}" if names else heading


def resolve_links(con) -> None:
    notes = con.execute("SELECT id, path, title, aliases FROM notes").fetchall()
    by_path, by_noext, by_stem, by_title = {}, {}, {}, {}
    for n in sorted(notes, key=lambda r: len(r["path"])):  # caminho mais curto vence em empate
        path = n["path"].lower()
        by_path.setdefault(path, n["id"])
        by_noext.setdefault(posixpath.splitext(path)[0], n["id"])
        by_stem.setdefault(posixpath.splitext(posixpath.basename(path))[0], n["id"])
        by_title.setdefault((n["title"] or "").lower(), n["id"])
        for a in json.loads(n["aliases"] or "[]"):
            by_title.setdefault(a.lower(), n["id"])
    paths = {n["id"]: n["path"] for n in notes}

    updates = []
    for row in con.execute("SELECT rowid, src, target, kind FROM links WHERE kind IN ('wikilink','mdlink')").fetchall():
        t = row["target"].strip().lower()
        dst = None
        if row["kind"] == "mdlink":
            base = posixpath.dirname(paths.get(row["src"], ""))
            full = posixpath.normpath(posixpath.join(base, t)).lstrip("./") if not t.startswith("/") else t.lstrip("/")
            dst = by_path.get(full) or by_noext.get(posixpath.splitext(full)[0]) or by_path.get(t)
        else:
            t = t.removesuffix(".md")
            dst = by_path.get(row["target"].strip().lower()) or by_noext.get(t) or by_stem.get(posixpath.basename(t)) or by_title.get(t)
        if dst == row["src"]:
            dst = None
        updates.append((dst, "EXTRACTED" if dst else None, row["rowid"]))
    con.executemany("UPDATE links SET dst=?, conf=? WHERE rowid=?", updates)


def _backfill_embeddings(con) -> int:
    # Em projetos pequenos, os vetores do corpo recuperam implementação descrita com outras palavras.
    # Em projetos grandes, limitamos memória, tempo de indexação e competição entre trechos no top 200.
    mode = os.environ.get("ATLASBRAIN_EMBED_CODIGO", "auto").lower()
    if mode not in ("auto", "all", "resumo"):
        mode = "auto"
    code_chunks = con.execute(
        "SELECT COUNT(*) FROM chunks c JOIN notes n ON n.id=c.note_id "
        "WHERE n.kind='codigo' AND c.ord>=0"
    ).fetchone()[0] if mode == "auto" else 0
    summary_only = mode == "resumo" or (mode == "auto" and code_chunks > AUTO_CODE_CHUNK_LIMIT)
    rows = con.execute(
        "SELECT c.id, c.note_id, c.heading, c.text, n.title FROM chunks c JOIN notes n ON n.id=c.note_id "
        "WHERE c.embedding IS NULL" + (" AND (n.kind != 'codigo' OR c.ord = -1)" if summary_only else "")
    ).fetchall()
    set_meta(con, 'embedding_cache_stats', json.dumps({'reutilizados':0,'calculados':0}))
    if not rows:
        return 0
    reused = calculated = 0
    _log(f"[atlasbrain] gerando embeddings de {len(rows)} trechos…")
    touched = set()
    batch = 256
    for i in range(0, len(rows), batch):
        part = rows[i : i + batch]
        texts = [f"{r['title']}\n{r['heading']}\n{r['text']}"[:2000] for r in part]
        keys = [hashlib.sha256((EMBED_MODEL+'\0'+t).encode()).hexdigest() for t in texts]
        cached = {k: con.execute('SELECT vector FROM embedding_cache WHERE key=?',(k,)).fetchone() for k in set(keys)}
        missing = list(dict.fromkeys(k for k in keys if cached[k] is None))
        blobs = {k: bytes(row[0]) for k,row in cached.items() if row is not None}
        if missing:
            vectors = embed.embed([texts[keys.index(k)] for k in missing])
            blobs.update({k:to_blob(v) for k,v in zip(missing,vectors)})
        calculated += len(missing)
        reused += len(keys)-len(missing)
        con.executemany('INSERT OR REPLACE INTO embedding_cache VALUES(?,?,?)',
                        [(k,blobs[k],time.time()) for k in set(keys)])
        con.executemany("UPDATE chunks SET embedding=? WHERE id=?", [(blobs[k], r["id"]) for k, r in zip(keys, part)])
        touched.update(r["note_id"] for r in part)
        con.commit()
        if len(rows) > batch:
            _log(f"[atlasbrain]   {min(i + batch, len(rows))}/{len(rows)}")
    for nid in touched:
        vs = [from_blob(r[0]) for r in con.execute("SELECT embedding FROM chunks WHERE note_id=? AND embedding IS NOT NULL", (nid,))]
        if vs:
            m = np.mean(vs, axis=0)
            m /= np.linalg.norm(m) or 1
            con.execute("UPDATE notes SET embedding=? WHERE id=?", (to_blob(m), nid))
    con.commit()
    # Bounded cache: old unused chunks must not grow the database indefinitely.
    con.execute('DELETE FROM embedding_cache WHERE key IN (SELECT key FROM embedding_cache ORDER BY touched DESC LIMIT -1 OFFSET 50000)')
    set_meta(con, 'embedding_cache_stats', json.dumps({'reutilizados':reused,'calculados':calculated}))
    con.commit()
    return len(rows)


def compute_similar(con) -> int:
    """Ligações inferidas: notas semanticamente próximas."""
    con.execute("DELETE FROM links WHERE kind='similar'")
    rows = con.execute("SELECT id, embedding FROM notes WHERE embedding IS NOT NULL").fetchall()
    if len(rows) < 2:
        return 0
    ids = np.array([r["id"] for r in rows])
    M = np.stack([from_blob(r["embedding"]) for r in rows])
    explicit = {
        frozenset((r[0], r[1]))
        for r in con.execute("SELECT src, dst FROM links WHERE dst IS NOT NULL AND kind!='similar'")
    }
    seen, out = set(), []
    k = min(SIMILAR_K, len(rows) - 1)
    for start in range(0, len(rows), 512):
        S = M[start : start + 512] @ M.T
        for i in range(S.shape[0]):
            S[i, start + i] = -1
        top = np.argpartition(-S, k, axis=1)[:, :k] if k < len(rows) - 1 else np.argsort(-S, axis=1)[:, :k]
        for i, cols in enumerate(top):
            a = int(ids[start + i])
            for j in cols:
                score = float(S[i, j])
                b = int(ids[j])
                pair = frozenset((a, b))
                if score < SIMILAR_MIN or pair in seen or pair in explicit:
                    continue
                seen.add(pair)
                out.append((a, str(b), b, "similar", round(score, 4), "INFERRED", f"similaridade {score:.2f}"))
    con.executemany("INSERT INTO links(src, target, dst, kind, weight, conf, detalhe) VALUES(?,?,?,?,?,?,?)", out)
    return len(out)


def index_vault(vault: Path, force: bool = False, quiet: bool = False, wait_timeout: float = 0) -> dict:
    log = (lambda *_: None) if quiet else _log
    with _IndexLock(vault, wait_timeout) as acquired:
        if not acquired:
            return {"skipped": True, "motivo": "outro processo está indexando este projeto agora"}
        con = connect(vault)
        t0 = time.time()
        if embed.enabled() and get_meta(con, "embed_model") not in (None, EMBED_MODEL):
            log("[atlasbrain] modelo de embeddings mudou; recalculando vetores")
            con.execute("UPDATE chunks SET embedding=NULL")
            con.execute("UPDATE notes SET embedding=NULL")
        saved = int(get_meta(con, "schema", "0") or 0)
        if saved > int(SCHEMA_VERSION):
            # índice feito por uma versão mais nova do atlasbrain (outro processo já atualizado): não mexe,
            # senão as duas versões reindexariam uma por cima da outra sem parar
            con.close()
            log(f"[atlasbrain] índice em formato {saved}, mais novo que este processo ({SCHEMA_VERSION}); reinicie-o")
            return {"skipped": True, "motivo": "versão antiga do atlasbrain"}
        if saved < int(SCHEMA_VERSION):
            force = True  # formato novo de índice: reprocessa uma vez
            log("[atlasbrain] atualizando o formato do índice (uma vez só)")
        files = scan(vault)
        existing = {r["path"]: r for r in con.execute("SELECT id, path, mtime, size, hash FROM notes")}
        _SKIPPED.clear()
        try:
            _SKIPPED.update({k: v for k, v in json.loads(get_meta(con, "pulados", "{}")).items()
                             if isinstance(v, list) and k in files})
        except ValueError:
            pass

        removed = [r for p, r in existing.items() if p not in files]
        for r in removed:
            _delete_note_content(con, r["id"])
            con.execute("DELETE FROM notes WHERE id=?", (r["id"],))

        changed = 0
        todo = []
        for rel, path in files.items():
            try:
                st = path.stat()
            except OSError:
                continue
            ex = existing.get(rel)
            skipped = _SKIPPED.get(rel)
            if not force and not ex and skipped and skipped[1] == st.st_mtime and skipped[2] == st.st_size:
                continue  # já sabemos que é lockfile/minificado/gerado e não mudou
            if force or not ex or ex["mtime"] != st.st_mtime or ex["size"] != st.st_size:
                todo.append((rel, path, st, ex))
        if todo:
            log(f"[atlasbrain] {len(todo)} arquivo(s) para processar em {vault}")
        for n, (rel, path, st, ex) in enumerate(todo, 1):
            try:
                if _index_file(con, rel, path, st, ex, force):
                    changed += 1
            except Exception as e:  # um arquivo ruim não derruba a indexação
                log(f"[atlasbrain] erro em {rel}: {e}")
            if n % 100 == 0:
                con.commit()
                log(f"[atlasbrain]   {n}/{len(todo)}")
        con.commit()

        embedded = 0
        similar = None
        if changed or removed or todo:
            resolve_links(con)
            code.resolve_code(con, vault)
            con.commit()
        if embed.enabled():
            embedded = _backfill_embeddings(con)
            set_meta(con, "embed_model", EMBED_MODEL)
        if changed or removed or embedded:
            similar = compute_similar(con) if embed.enabled() else None
            set_meta(con, "rev", int(get_meta(con, "rev", "0")) + 1)
            try:
                from . import report
                report.write(con, vault)
            except Exception as e:
                log(f"[atlasbrain] relatório não gerado: {e}")
        from .intelligence import sync_links, capture
        sync_links(con)
        capture(con)
        set_meta(con, "last_indexed", time.time())
        set_meta(con, "schema", SCHEMA_VERSION)
        set_meta(con, "pulados", json.dumps(_SKIPPED, ensure_ascii=False))
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        cache_stats=json.loads(get_meta(con,'embedding_cache_stats','{}')) if embed.enabled() else {}
        con.close()
        stats = {
            'cache_embeddings':cache_stats,
            "pulados": len(_SKIPPED),
            "arquivos": total, "alterados": changed, "removidos": len(removed),
            "trechos_vetorizados": embedded, "ligacoes_semanticas": similar,
            "segundos": round(time.time() - t0, 2),
        }
        if changed or removed or embedded:
            log(f"[atlasbrain] índice atualizado: {stats}")
        return stats
