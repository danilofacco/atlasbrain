"""Interface web local (só 127.0.0.1): busca + grafo interativo + leitor de notas."""

import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import graph as g
from .queries import connections, dependencies
from .config import invalid_folder_reason, register, registered, unregister
from .db import connect, get_meta
from .indexer import _log, index_vault, scan
from .search import Searcher

STATIC = Path(__file__).parent / "static"


def serve(vault: Path, port: int = 8765, auto_index: bool = True, open_browser: bool = True) -> None:
    from .service import start
    from urllib.parse import urlencode
    import webbrowser
    live = start(vault, port, auto_index)
    url = f"http://127.0.0.1:{live['port']}/?" + urlencode({'v': str(vault)})
    _log(f"[atlasbrain] interface em {url} (PID {live['pid']})")
    if open_browser:
        webbrowser.open(url)


def create_server(vault: Path, port: int = 8765, auto_index: bool = True, *, handler_only=False) -> ThreadingHTTPServer:
    """Um servidor para todos os cérebros: `?v=<pasta>` escolhe o projeto (padrão: o de onde foi aberto).
    port=0 escolhe uma porta livre (testes)."""
    default = str(vault.resolve())
    brains: dict[str, tuple] = {}
    brains_lock = threading.Lock()

    def get_brain(v: str | None):
        key = str(Path(v).expanduser().resolve()) if v else default
        with brains_lock:
            if key not in brains:
                if key != default and key not in {b["path"] for b in registered()}:
                    key = default  # só abre cérebros já registrados (nada de caminho arbitrário)
                if key not in brains:
                    c = connect(Path(key))
                    brains[key] = (Path(key), c, Searcher(c), threading.Lock())
            return brains[key]

    get_brain(None)
    FILE_LIMIT = 25000
    workspaces: dict[str, dict] = {}  # pastas abertas pela interface: contando → indexando → pronto | erro

    def open_folder(path: Path) -> None:
        key = str(path)
        try:
            workspaces[key] = {"estado": "contando", "arquivos": 0}
            n = len(scan(path))  # só lista (git ls-files ou os.walk); ainda não cria nada na pasta
            if n > FILE_LIMIT:
                workspaces[key] = {"estado": "erro", "arquivos": n,
                                  "msg": f"{n} arquivos: grande demais para um cérebro. Escolha uma subpasta "
                                         f"ou liste o que ignorar em .atlasbrainignore."}
                return
            workspaces[key] = {"estado": "indexando", "arquivos": n}
            register(path)
            index_vault(path)
            workspaces[key] = {"estado": "pronto", "arquivos": n}
        except Exception as e:
            workspaces[key] = {"estado": "erro", "msg": str(e)}

    if auto_index:
        def loop():
            while True:
                for b in list(brains.values()):
                    try:
                        index_vault(b[0])
                    except Exception as e:
                        _log(f"[atlasbrain] falha ao indexar {b[0]}: {e}")
                time.sleep(30)
        threading.Thread(target=loop, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _seguro(self) -> bool:
            """Ações que mexem no disco só aceitam a própria interface: outro site aberto no navegador não
            consegue mandar o cabeçalho X-atlasbrain sem preflight, e o Host precisa ser o local."""
            host = (self.headers.get("Host") or "").split(":")[0]
            origin = self.headers.get("Origin")
            ok_origin = origin is None or re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin)
            return self.headers.get("X-atlasbrain") == "1" and host in ("127.0.0.1", "localhost") and bool(ok_origin)

        def do_POST(self):
            u = urlparse(self.path)
            if not self._seguro():
                return self._json({"erro": "origem não permitida"}, 403)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if u.path=='/api/editor' and (n<0 or n>1_500_000):
                    return self._json({'erro':'Requisição maior que o limite do editor'},413)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                return self._json({"erro": "corpo inválido"}, 400)
            if u.path == '/api/historico/restaurar':
                from . import editor
                from .indexer import index_vault
                q = {k:v[0] for k,v in parse_qs(u.query).items()}
                vault, con, searcher, lock = get_brain(q.get('v'))
                try:
                    if not isinstance(body, dict):
                        raise editor.EditError('Corpo inválido')
                    result = editor.restore(vault, body.get('path'), body.get('version'), body.get('revision'))
                    try:
                        result['indice'] = index_vault(vault, quiet=True, wait_timeout=5)
                    except Exception:
                        result['aviso'] = 'Restauração concluída; índice pendente.'
                    return self._json(result)
                except editor.EditError as e:
                    return self._json({'erro': str(e)}, e.status)
                except OSError as e:
                    return self._json({'erro': str(e)}, 400)
            if u.path == '/api/arquivo/excluir':
                from . import editor
                from .indexer import index_vault
                q = {k:v[0] for k,v in parse_qs(u.query).items()}
                vault, con, searcher, lock = get_brain(q.get('v'))
                try:
                    if not isinstance(body, dict) or not isinstance(body.get('path'), str):
                        raise editor.EditError('Corpo inválido')
                    with lock:
                        row = con.execute('SELECT path FROM notes WHERE path=?', (body['path'],)).fetchone()
                        if not row:
                            raise editor.EditError('Arquivo não encontrado no índice', 404)
                        result = editor.delete(vault, row['path'])
                    try:
                        result['indice'] = index_vault(vault, quiet=True, wait_timeout=5)
                    except Exception:
                        result['aviso'] = 'Arquivo removido; atualização do índice pendente.'
                    return self._json(result)
                except editor.EditError as e:
                    return self._json({'erro': str(e)}, e.status)
                except OSError as e:
                    return self._json({'erro': 'Não consegui remover o arquivo: ' + str(e)}, 400)
            if u.path == '/api/editor':
                from . import editor
                from .indexer import index_vault
                q = {k:v[0] for k,v in parse_qs(u.query).items()}
                vault, con, searcher, lock = get_brain(q.get('v'))
                try:
                    if not isinstance(body, dict) or not isinstance(body.get('create',False),bool):
                        raise editor.EditError('Corpo inválido')
                    result=editor.save(vault, body.get('path'), body.get('content'), body.get('revision'), body.get('create',False))
                    result['indice']=index_vault(vault,quiet=True,wait_timeout=5)
                    return self._json(result)
                except editor.EditError as e:
                    return self._json({'erro':str(e)}, e.status)
                except OSError as e:
                    return self._json({'erro':'Não consegui salvar o arquivo: '+str(e)},400)
            if u.path == "/api/vinculo":
                from . import intelligence as intelligence
                from .indexer import index_vault
                q = {k:v[0] for k,v in parse_qs(u.query).items()}
                vault, con, searcher, lock = get_brain(q.get('v'))
                try:
                    with lock:
                        result = intelligence.register_link(con, vault, body.get('de',''), body.get('para',''), body.get('motivo',''))
                    result['indice'] = index_vault(vault, quiet=True, wait_timeout=5)
                    return self._json(result)
                except (ValueError, OSError) as e:
                    return self._json({'erro':str(e)},400)
            if u.path == "/api/escolher":  # seletor de pastas nativo do macOS
                r = subprocess.run(["osascript", "-e",
                                    'POSIX path of (choose folder with prompt "Escolha a pasta que vai virar um cérebro atlasbrain")'],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    return self._json({"cancelado": True})
                return self._json({"path": r.stdout.strip().rstrip("/") or "/"})
            if u.path == "/api/cerebros":
                path = Path(str(body.get("path", "")).strip()).expanduser()
                try:
                    path = path.resolve()
                except OSError:
                    return self._json({"erro": "Caminho inválido."}, 400)
                reason = invalid_folder_reason(path)
                if reason:
                    return self._json({"erro": reason}, 400)
                if workspaces.get(str(path), {}).get("estado") not in ("contando", "indexando"):
                    threading.Thread(target=open_folder, args=(path,), daemon=True).start()
                    workspaces[str(path)] = {"estado": "contando", "arquivos": 0}
                return self._json({"ok": True, "path": str(path)})
            if u.path == "/api/esquecer":  # tira da lista (não apaga nada do disco)
                path = Path(str(body.get("path", ""))).expanduser().resolve()
                unregister(path)
                with brains_lock:
                    brains.pop(str(path), None)
                return self._json({"ok": True})
            return self._json({"erro": "rota desconhecida"}, 404)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            vault, con, searcher, lock = get_brain(q.get("v"))
            try:
                if u.path == "/api/versao":  # a página recarrega sozinha quando a interface muda
                    from .reload import version
                    return self._json({"versao": "%d-%d" % (version(True)[0] * 1000, version(True)[1])})
                if u.path == "/api/mcp":
                    from .service import project_id
                    return self._json({"path": f"/projects/{project_id(vault)}/mcp", "project": str(vault)})
                if u.path == "/api/brains":
                    current = q.get("v") and str(Path(q["v"]).expanduser().resolve())
                    return self._json({"atual": current if current in workspaces else str(vault),
                                       "cerebros": registered(), "trabalhos": workspaces})
                if u.path == "/api/pastas":  # sugestões para quem digita o caminho
                    if not self._seguro():
                        return self._json([], 403)
                    raw = q.get("p", "")
                    p = Path(raw).expanduser()
                    base, pref = (p, "") if raw.endswith("/") else (p.parent, p.name.lower())
                    try:
                        items = sorted(x for x in base.iterdir() if x.is_dir() and not x.name.startswith(".")
                                       and x.name.lower().startswith(pref))[:30]
                    except OSError:
                        items = []
                    return self._json([{"path": str(x), "projeto": (x / ".git").exists() or (x / ".atlasbrain").exists()}
                                       for x in items])
                if u.path in ("/", "/index.html"):
                    body = (STATIC / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path in ('/api/estado', '/api/mudancas', '/api/sugestoes', '/api/filtros'):
                    from . import intelligence as intelligence
                    with lock:
                        if u.path == '/api/estado':
                            return self._json(intelligence.status(con, vault, int(q.get('limite',30)), int(q.get('offset',0))))
                        if u.path == '/api/mudancas':
                            return self._json(intelligence.changes(con, int(q.get('offset',0)), int(q.get('limite',30))))
                        if u.path == '/api/sugestoes':
                            return self._json(intelligence.suggestions(con, q.get('path','')))
                        return self._json({'extensoes':sorted({Path(r[0]).suffix for r in con.execute('SELECT path FROM notes') if Path(r[0]).suffix})})
                elif u.path == '/api/historico':
                    if not self._seguro():
                        return self._json({'erro':'origem não permitida'}, 403)
                    from . import editor
                    try:
                        result = editor.history(vault, q.get('path'), q.get('version'))
                        return self._json(result)
                    except editor.EditError as e:
                        return self._json({'erro':str(e)}, e.status)
                elif u.path == '/api/editor/links':
                    from . import editor
                    with lock:
                        if 'ref' in q:
                            return self._json({'nota':editor.resolve_reference(con,q['ref'])})
                        return self._json({'itens':editor.link_candidates(con,q.get('q',''),q.get('path',''))})
                elif u.path == '/api/editor':
                    from . import editor
                    if not self._seguro():
                        return self._json({'erro':'origem não permitida'},403)
                    try:
                        return self._json(editor.read(vault,q.get('path',''),q.get('previous')=='1'))
                    except editor.EditError as e:
                        return self._json({'erro':str(e)},e.status)
                elif u.path == "/api/soltos":
                    with lock:
                        self._json(g.isolated_report(con, q.get("pasta", ""), int(q.get("limite", 50)), int(q.get("offset", 0))))
                elif u.path == "/api/graph":
                    with lock:
                        G = g.build_graph(con, similar=q.get("similar", "1") == "1",
                                          tags=q.get("tags") == "1", ghosts=q.get("ghosts", "1") == "1",
                                          origin=q.get("origem", ""), relation=q.get("relacao", ""))
                    folder=q.get('pasta','').strip('/')
                    ext=q.get('ext','')
                    kind=q.get('kind','')
                    show_orphans=q.get('orphans','1')=='1'
                    if folder or ext or kind or not show_orphans:
                        keep={n for n,d in G.nodes(data=True) if d.get('path') and
                              (not folder or d['path'].startswith(folder+'/')) and
                              (not ext or Path(d['path']).suffix==ext) and (not kind or d['kind']==kind) and
                              (show_orphans or not d.get('isolated') or d.get('tipo') in ('decisao','aprendizado'))}
                        G=G.subgraph(keep).copy()
                    try:
                        limit = max(1, min(int(q.get("limite", 500)), 800))
                        page = int(q.get("pagina", 0))
                    except ValueError:
                        return self._json({"erro": "Page size and page must be integers."}, 400)
                    v = g.view(G, q.get("prefixo", ""), limit=limit,
                                page=page, focus=q.get("foco", ""))
                    self._json({"vault": vault.name, **v})
                elif u.path in ("/api/conexoes", "/api/dependencias"):
                    with lock:
                        row = g.find_note(con, q.get("path", ""))
                        if not row:
                            return self._json({"erro": "não encontrada"}, 404)
                        if u.path == "/api/conexoes":
                            return self._json(connections(con, row["id"], limit=min(int(q.get("limite", 12)), 30)))
                        return self._json({"centro": row["id"], **dependencies(con, row["id"], min(int(q.get("prof", 2)), 3))})
                elif u.path == "/api/search":
                    with lock:
                        self._json(searcher.search(q.get("q", ""), limit=int(q.get("limit", 20)), tag=q.get("tag")))
                elif u.path == "/api/note":
                    with lock:
                        row = g.find_note(con, q.get("path", ""))
                        if not row:
                            return self._json({"erro": "não encontrada"}, 404)
                        nb = g.neighbors(con, row["id"])
                        chunks = [c[0] for c in con.execute("SELECT text FROM chunks WHERE note_id=? ORDER BY ord", (row["id"],))]
                    p = vault / row["path"]
                    content = p.read_text(encoding="utf-8", errors="replace") if row["kind"] != "documento" and p.exists() else "\n\n".join(chunks)
                    self._json({"path": row["path"], "title": row["title"], "kind": row["kind"],
                                "mtime": row["mtime"], "frontmatter": json.loads(row["frontmatter"] or "{}"),
                                "content": content[:200_000], **nb})
                elif u.path == "/api/decisions":
                    with lock:
                        rows = con.execute("SELECT path, title, mtime, frontmatter, preview FROM notes").fetchall()
                    out = []
                    for r in rows:
                        fm = json.loads(r["frontmatter"] or "{}")
                        if fm.get("tipo") not in ("decisao", "aprendizado"):
                            continue
                        summary = r["preview"] or ""
                        m = re.search(r"## (?:Decisão|Decision)\s+(.+?)(?:\s+## |$)", summary)
                        out.append({"path": r["path"], "title": r["title"], "tipo": fm["tipo"],
                                    "data": str(fm.get("data") or ""), "projeto": (fm.get("projeto") or "").strip("[]"),
                                    "status": fm.get("status"), "origem": fm.get("origem"),
                                    "tags": [t for t in (fm.get("tags") or []) if t not in ("decisao", "aprendizado")],
                                    "resumo": (m.group(1) if m else re.sub(r"^#\s+" + re.escape(r["title"]) + r"\s*", "", summary))[:260],
                                    "mtime": r["mtime"]})
                    out.sort(key=lambda x: (x["data"], x["mtime"]), reverse=True)
                    self._json(out)
                elif u.path == "/api/overview":
                    with lock:
                        q1 = lambda sql: con.execute(sql).fetchone()[0]
                        kinds = {r[0]: r[1] for r in con.execute(
                            "SELECT json_extract(frontmatter, '$.tipo') t, COUNT(*) FROM notes WHERE t IS NOT NULL GROUP BY t")}
                        recent = [dict(path=r["path"], title=r["title"], kind=r["kind"], mtime=r["mtime"],
                                       **{"tipo": json.loads(r["frontmatter"] or "{}").get("tipo")} )
                                  for r in con.execute("SELECT path, title, kind, mtime, frontmatter FROM notes ORDER BY mtime DESC LIMIT 40")]
                        self._json({"vault": vault.name, "path": str(vault), "arquivos": q1("SELECT COUNT(*) FROM notes"),
                                    "ligacoes": q1("SELECT COUNT(*) FROM links WHERE dst IS NOT NULL"),
                                    "decisoes": kinds.get("decisao", 0), "aprendizados": kinds.get("aprendizado", 0),
                                    "tags": q1("SELECT COUNT(DISTINCT tag) FROM tags"),
                                    "indexado": float(get_meta(con, "last_indexed", "0")), "recentes": recent})
                elif u.path == "/api/open":
                    if not self._seguro():
                        return self._json({"erro": "origem não permitida"}, 403)
                    row = g.find_note(con, q.get("path", ""))
                    if row:
                        flags = ["-R"] if q.get("reveal") else []
                        subprocess.Popen(["open", *flags, str(vault / row["path"])])
                    self._json({"ok": bool(row)})
                else:
                    self._json({"erro": "rota desconhecida"}, 404)
            except BrokenPipeError:
                pass

    if handler_only:
        return H
    return ThreadingHTTPServer(("127.0.0.1", port), H)
