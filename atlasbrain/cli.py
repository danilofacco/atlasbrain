import argparse
import json
import shutil
import shlex
import subprocess
import sys
from pathlib import Path

from .config import resolve_vault


def _vault_arg(p):
    p.add_argument("--vault", "-v", help="pasta do segundo cérebro (padrão: $ATLASBRAIN_VAULT ou a pasta atual)")


def cmd_index(a):
    from .indexer import index_vault

    stats = index_vault(resolve_vault(a.vault), force=a.force, quiet=a.quiet,
                        wait_timeout=0 if a.quiet else 900)  # na mão: espera o ciclo automático; git hook: não
    if not a.quiet:
        print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_search(a):
    from .db import connect
    from .search import Searcher

    res = Searcher(connect(resolve_vault(a.vault))).search(" ".join(a.query), limit=a.limit, tag=a.tag)
    for i, r in enumerate(res, 1):
        head = f" › {r['heading']}" if r["heading"] else ""
        print(f"{i:>2}. {r['title']}  [{r['path']}{head}]  ({r['score']})")
        print(f"    {r['snippet'][:220]}")
    if not res:
        print("Nada encontrado.")


def cmd_service(a):
    from . import service
    try:
        if a.cmd == 'status':
            print(json.dumps(service.owned_health() or {'running': False}, indent=2))
        elif a.cmd == 'stop':
            print('Stopped.' if service.stop() else 'Not running.')
        elif a.cmd == '_daemon':
            service.run_daemon(resolve_vault(a.vault), a.port, not a.no_auto)
        else:
            vault = resolve_vault(a.vault)
            from .config import invalid_folder_reason, GLOBAL_BRAIN
            if vault != GLOBAL_BRAIN.resolve() and (reason := invalid_folder_reason(vault)):
                raise RuntimeError(reason)
            live = service.start(vault, a.port, not a.no_auto)
            if a.cmd == 'setup':
                print(service.client_config(vault, a.client, a.port, a.name), end='')
                print(f"Service ready: PID {live['pid']}, port {live['port']}. Paste the configuration into your client.", file=sys.stderr)
            else:
                print(json.dumps(live, indent=2))
                if a.cmd == 'serve' and not a.no_open:
                    import webbrowser
                    from urllib.parse import urlencode
                    webbrowser.open(f"http://127.0.0.1:{a.port}/?" + urlencode({'v': str(vault)}))
    except (RuntimeError, OSError) as e:
        raise SystemExit(str(e)) from e


def cmd_serve(a):
    cmd_service(a)


def cmd_update(a):
    from . import update
    if a.action == 'enable':
        result = update.configure(True)
    elif a.action == 'disable':
        result = update.configure(False)
    elif a.action == 'status':
        result = update.status()
    elif a.action == 'check':
        result = update.check()
    else:
        result = update.apply(vault=Path(a.vault).resolve() if a.vault else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('state') in ('error', 'failed'):
        raise SystemExit(1)


def cmd_graph(a):
    import threading
    from types import SimpleNamespace
    from .db import connect
    from .search import Searcher
    from . import tools as f

    vault = resolve_vault(a.vault)
    con = connect(vault)
    ctx = SimpleNamespace(vault=vault, con=con, db_lock=threading.Lock(), searcher=Searcher(con))
    try:
        if a.operation_id == 'consultar':
            result = f.query_graph(ctx, ' '.join(a.query), mode=a.mode, depth=a.depth,
                                      tokens=a.tokens, direction=a.direction, relations=a.relation)
        elif a.operation_id == 'impacto':
            result = f.impact(ctx, a.target, depth=a.depth, tokens=a.tokens,
                               include_inferred=a.include_inferred)
        else:
            result = f.graph_path(ctx, a.source, a.target, direction=a.direction, tokens=a.tokens)
        print(result)
    finally:
        con.close()


def cmd_mcp(a):
    from .mcp_server import run

    run(resolve_vault(a.vault), auto_index=not a.no_auto)


def cmd_import(a):
    from .url_import import import_url
    from .indexer import index_vault
    vault = resolve_vault(a.vault)
    try:
        result = import_url(vault, a.url, a.title, a.update)
        if result['importada']:
            index_vault(vault)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as e:
        raise SystemExit(str(e)) from e


def cmd_stats(a):
    from .db import connect, get_meta

    con = connect(resolve_vault(a.vault))
    q = lambda s: con.execute(s).fetchone()[0]
    print(json.dumps({
        "arquivos": q("SELECT COUNT(*) FROM notes"),
        "trechos": q("SELECT COUNT(*) FROM chunks"),
        "com_embedding": q("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"),
        "links_explicitos": q("SELECT COUNT(*) FROM links WHERE kind!='similar' AND dst IS NOT NULL"),
        "links_para_notas_inexistentes": q("SELECT COUNT(*) FROM links WHERE kind='wikilink' AND dst IS NULL"),
        "ligacoes_semanticas": q("SELECT COUNT(*) FROM links WHERE kind='similar'"),
        "tags": q("SELECT COUNT(DISTINCT tag) FROM tags"),
        "modelo": get_meta(con, "embed_model"),
    }, ensure_ascii=False, indent=2))


def cmd_connect(a):
    from .service import start, project_id
    vault = resolve_vault(a.vault)
    live = start(vault)
    url = f"http://127.0.0.1:{live['port']}/projects/{project_id(vault)}/mcp"
    targets = []
    if a.claude or not (a.claude or a.codex):
        targets.append(('claude', ['mcp', 'add', '--transport', 'http', '--scope', 'user', a.name, url]))
    if a.codex or not (a.claude or a.codex):
        targets.append(('codex', ['mcp', 'add', a.name, '--url', url]))
    for tool, args in targets:
        if not shutil.which(tool):
            print(f'{tool}: command not found; use setup to generate configuration.')
            continue
        result = subprocess.run([tool, *args], capture_output=True, text=True)
        print((result.stdout + result.stderr).strip())
        if result.returncode:
            raise SystemExit(result.returncode)


def cmd_capture(a):
    from . import capture

    if not a.worker and not a.transcript:
        capture.hook(a.vault, a.event)  # o projeto sai do cwd que vem no JSON do hook
        return
    vault = resolve_vault(a.vault, cwd=a.cwd or None)
    if a.worker:
        capture.worker(vault, Path(a.transcript), a.session_id, a.cwd, a.event)
    elif a.transcript:  # manual: extrai de um transcript específico agora
        capture.worker(vault, Path(a.transcript), a.session_id or a.transcript, a.cwd or "", "SessionEnd")


def cmd_briefing(a):
    from .capture import briefing

    cwd = a.cwd
    if not cwd and not sys.stdin.isatty():
        try:
            cwd = json.load(sys.stdin).get("cwd", "")
        except Exception:
            cwd = ""
    try:
        text = briefing(resolve_vault(a.vault, cwd=cwd or None), cwd or "")
    except Exception:
        text = ""  # hook de início de sessão nunca pode travar a sessão
    if text:
        print(text)


GUARD_TXT = ("This project has AtlasBrain (code graph and project memory). Before scanning files, "
             "prefer its MCP tools: find_files (names/symbols), symbol_location (definitions/usages), "
             "explain (dependencies) and search (content and meaning). "
             "If MCP is unavailable or finds nothing useful, use ordinary file tools.")

BLOCK_START, BLOCK_END = "<!-- atlasbrain:inicio -->", "<!-- atlasbrain:fim -->"
INSTRUCTION_BLOCK = f"""{BLOCK_START}
## AtlasBrain — project second brain
Each project has a local `.atlasbrain/` brain (code graph, decisions and learnings), exposed by the `atlasbrain` MCP.
- Before grep/glob, use `find_files` (names/symbols), `symbol_location` (definitions/usages),
  `explain` (dependencies and rationale), `graph_path` and `report`.
- For task context and history: `task_context`, `search`, `decisions`, `read` and `related`.
- INFERRED relations are clues; EXTRACTED relations have code/text evidence.
- Record important finalized decisions with `record_decision` (`supersedes` replaces an earlier decision);
  record non-obvious discoveries with `record_learning`.
- On a global endpoint, pass `project_folder` with the absolute folder where you are working.
{BLOCK_END}
"""


def _instruction_block(file_path: Path, remove: bool) -> None:
    txt = file_path.read_text() if file_path.exists() else ""
    if BLOCK_START in txt:
        a, b = txt.index(BLOCK_START), txt.index(BLOCK_END) + len(BLOCK_END)
        txt = (txt[:a].rstrip() + "\n" + txt[b:].lstrip("\n")).strip() + "\n"
    if not remove:
        txt = txt.rstrip() + ("\n\n" if txt.strip() else "") + INSTRUCTION_BLOCK
    file_path.write_text(txt)


def _guard_relevant(data):
    tool = data.get("tool_name", "")
    if tool in ("Read", "Glob", "Grep"):
        return True
    if tool not in ("Bash", "exec_command", "shell_command", "shell", "local_shell"):
        return False
    command = data.get("tool_input", {}).get("command", "") or data.get("tool_input", {}).get("cmd", "")
    if isinstance(command, list):
        command = shlex.join(command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    start = True
    for token in lexer:
        if token and all(c in ";&|()" for c in token):
            start = True
        elif start:
            if token in ("sudo", "command", "env") or "=" in token or token.startswith("-"):
                continue
            if Path(token).name in {"rg", "grep", "egrep", "fgrep", "find", "fd", "cat", "head", "tail", "sed", "awk", "less", "more"}:
                return True
            start = False
    return False


def cmd_guard(a):
    """Non-blocking reminder before code discovery/read, once per project/session every 20 min."""
    try:
        data = json.load(sys.stdin)
        if not _guard_relevant(data):
            return
        from .config import BRAIN, find_project
        project_root = find_project(Path(data.get("cwd") or "."))
        if not project_root or not (project_root / BRAIN).is_dir():
            return
        state_f = Path.home() / ".cache" / "atlasbrain" / "guard.json"
        state_f.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        import time
        # Concurrent tool hooks must not inject the same reminder twice.
        with state_f.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(state_f.read_text()) if state_f.exists() else {}
            except ValueError:
                state = {}
            sid = str(project_root) + ":" + str(data.get("session_id", "?"))
            now = time.time()
            if now - state.get(sid, 0) < 1200:
                return
            state = {k: v for k, v in state.items() if now - v < 86400}
            state[sid] = now
            state_f.write_text(json.dumps(state))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": GUARD_TXT}}))
    except Exception:
        pass  # hook failures never block the tool


def cmd_init(a):
    """Transforma a pasta (ou o projeto atual) num cérebro: cria .atlasbrain/, indexa e instala git hooks."""
    vault = Path(a.folder).expanduser().resolve() if a.folder else resolve_vault()
    from .config import BRAIN, data_dir
    data_dir(vault)
    print(f"✓ cérebro em {vault / BRAIN}")
    hooks_dir = vault / ".git" / "hooks"
    if hooks_dir.is_dir() and not a.no_git:
        exe = shutil.which("atlasbrain") or str(Path(sys.argv[0]).resolve())
        line = f'"{exe}" index --vault "{vault}" --quiet >/dev/null 2>&1 &  # atlasbrain'
        for h in ("post-commit", "post-checkout", "post-merge"):
            f = hooks_dir / h
            txt = f.read_text() if f.exists() else "#!/bin/sh\n"
            if "# atlasbrain" not in txt:
                f.write_text(txt.rstrip() + "\n" + line + "\n")
                f.chmod(0o755)
        print("✓ git hooks: reindexa em segundo plano a cada commit, checkout e merge")
    from .indexer import index_vault
    print(json.dumps(index_vault(vault), ensure_ascii=False))


def cmd_bench(a):
    from . import bench
    from .config import BRAIN
    vault = resolve_vault(a.vault)
    f = Path(a.file_path) if a.file_path else vault / BRAIN / "benchmark.jsonl"
    if not f.exists():
        raise SystemExit(f"Sem perguntas em {f}. Formato: uma linha JSON por pergunta "
                         '{"pergunta": "...", "esperado": ["caminho/arquivo"], "tipo": "conceito"}')
    res = bench.run_benchmark(vault, bench.load_questions(f))
    print(bench.report(res, details=a.details))
    if a.json:
        Path(a.json).write_text(json.dumps({k: bench.metrics(v) for k, v in res.items()}, ensure_ascii=False, indent=2))


def _remove_owned_hooks(hooks):
    kept = []
    for entry in hooks:
        handlers = [h for h in entry.get("hooks", []) if not (
            "atlasbrain" in h.get("command", "") and
            any(k in h.get("command", "") for k in ("capturar", "briefing", " guard")))]
        if handlers:
            kept.append({**entry, "hooks": handlers})
    return kept


def _merge_hook(settings: Path, event: str, command: str) -> bool:
    data = json.loads(settings.read_text()) if settings.exists() else {}
    hooks = data.setdefault("hooks", {}).setdefault(event, [])
    hooks[:] = _remove_owned_hooks(hooks)
    entry = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    if event == "PreToolUse":
        entry["matcher"] = "Bash|Read|Grep|Glob"
    hooks.append(entry)
    settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def cmd_hooks(a):
    exe = shutil.which("atlasbrain") or str(Path(sys.argv[0]).resolve())
    fixed = f' --vault "{resolve_vault(a.vault)}"' if a.vault else ""  # sem --vault: cérebro do projeto da sessão
    cmd = f'"{exe}" capturar{fixed}'
    brief = f'"{exe}" briefing{fixed}'
    guard = f'"{exe}" guard'
    targets = [(Path.home() / ".claude" / "settings.json", ["Stop", "SessionEnd", "SessionStart", "PreToolUse"]),
               (Path.home() / ".codex" / "hooks.json", ["Stop", "SessionStart", "PreToolUse"])]
    for f, events in targets:
        if not f.parent.exists():
            continue
        for ev in events:
            c = brief if ev == "SessionStart" else guard if ev == "PreToolUse" else cmd
            if a.remove:
                data = json.loads(f.read_text()) if f.exists() else {}
                lst = data.get("hooks", {}).get(ev, [])
                lst[:] = _remove_owned_hooks(lst)
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            else:
                _merge_hook(f, ev, c)
        print(f"{'removido de' if a.remove else '✓ hook em'} {f} ({', '.join(events)})")
    for f in (Path.home() / ".claude" / "CLAUDE.md", Path.home() / ".codex" / "AGENTS.md"):
        if f.parent.exists():
            _instruction_block(f, a.remove)
            print(f"{'removido de' if a.remove else '✓ instruções em'} {f}")


def main():
    ap = argparse.ArgumentParser(prog=Path(sys.argv[0]).name if Path(sys.argv[0]).name in ("atlasbrain",) else "atlasbrain", description="Segundo cérebro local sobre uma pasta.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for command in ('setup', 'start', '_daemon'):
        p = sub.add_parser(command, help='shared local HTTP service' if command != '_daemon' else argparse.SUPPRESS)
        _vault_arg(p)
        p.add_argument('--port', '--porta', type=int, default=8765)
        p.add_argument('--no-auto', '--sem-auto', dest='no_auto', action='store_true')
        if command == 'setup':
            p.add_argument('--client', choices=['claude', 'antigravity', 'codex'], required=True)
            p.add_argument('--name', '--nome', dest='name', default='atlasbrain')
        p.set_defaults(fn=cmd_service)
    for command in ('status', 'stop'):
        p = sub.add_parser(command, help='inspect/stop the shared service')
        p.set_defaults(fn=cmd_service)

    p = sub.add_parser('update', help='check, apply or configure version-gated updates')
    p.add_argument('action', choices=('status', 'check', 'apply', 'enable', 'disable'))
    p.add_argument('--vault', help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser('import-url', aliases=['importar-url'], help='import a public web page or PDF')
    _vault_arg(p)
    p.add_argument('url')
    p.add_argument('--title', '--titulo', dest='title')
    p.add_argument('--update', '--atualizar', dest='update', action='store_true')
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("index", help="indexa (incremental) a pasta")
    _vault_arg(p)
    p.add_argument("--force", action="store_true", help="reprocessa tudo")
    p.add_argument("--quiet", "--silencioso", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("search", aliases=["buscar"], help="busca híbrida no terminal")
    _vault_arg(p)
    p.add_argument('query', nargs="+")
    p.add_argument('--limit', '--limite', "-n", dest='limit', type=int, default=10)
    p.add_argument("--tag")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser('query-graph', aliases=['consultar'], help='busca e percorre o grafo de arquivos/símbolos')
    _vault_arg(p)
    p.add_argument('query', nargs='+')
    p.add_argument('--mode', '--modo', dest='mode', choices=['bfs', 'dfs'], default='bfs')
    p.add_argument('--depth', '--profundidade', dest='depth', type=int, choices=range(7), default=2)
    p.add_argument('--direction', '--direcao', dest='direction', choices=['entrada', 'saida', 'ambas'], default='ambas')
    p.add_argument('--relation', '--relacao', dest='relation', action='append', help='filtra relações; pode repetir')
    p.add_argument('--tokens', type=int, default=2000)
    p.set_defaults(fn=cmd_graph, operation_id='consultar')

    p = sub.add_parser('impact', aliases=['impacto'], help='dependentes afetados por uma alteração')
    _vault_arg(p)
    p.add_argument('target')
    p.add_argument('--depth', '--profundidade', dest='depth', type=int, choices=range(7), default=3)
    p.add_argument('--include-inferred', '--incluir-inferidas', dest='include_inferred', action='store_true')
    p.add_argument('--tokens', type=int, default=2000)
    p.set_defaults(fn=cmd_graph, operation_id='impacto')

    p = sub.add_parser('graph-path', aliases=['caminho'], help='caminho entre arquivos ou símbolos')
    _vault_arg(p)
    p.add_argument('source')
    p.add_argument('target')
    p.add_argument('--direction', '--direcao', dest='direction', choices=['entrada', 'saida', 'ambas'], default='ambas')
    p.add_argument('--tokens', type=int, default=2000)
    p.set_defaults(fn=cmd_graph, operation_id='caminho')

    p = sub.add_parser("serve", help="abre a interface web com o grafo")
    _vault_arg(p)
    p.add_argument('--port', '--porta', type=int, default=8765)
    p.add_argument('--no-auto', '--sem-auto', dest="no_auto", action="store_true", help="não reindexa automaticamente")
    p.add_argument('--no-open', '--nao-abrir', dest="no_open", action="store_true", help="não abre o navegador")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("mcp", help="servidor MCP (stdio) para Claude/Codex")
    _vault_arg(p)
    p.add_argument('--no-auto', '--sem-auto', dest="no_auto", action="store_true")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("stats", help="números do índice")
    _vault_arg(p)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser('connect', aliases=['conectar'], help="registra o MCP no Claude Code e no Codex")
    _vault_arg(p)
    p.add_argument("--claude", action="store_true")
    p.add_argument("--codex", action="store_true")
    p.add_argument('--name', '--nome', dest='name', default="atlasbrain")
    p.set_defaults(fn=cmd_connect)

    p = sub.add_parser('capture', aliases=['capturar'], help="captura decisões/aprendizados de um transcript (usado pelos hooks)")
    _vault_arg(p)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--transcript")
    p.add_argument('--session-id', '--sessao', dest='session_id', default="")
    p.add_argument("--cwd", default="")
    p.add_argument('--event', '--evento', dest='event')
    p.set_defaults(fn=cmd_capture)

    p = sub.add_parser("bench", help="mede a qualidade da busca com perguntas de resposta conhecida")
    _vault_arg(p)
    p.add_argument('--file', '--arquivo', dest='file_path', help="JSONL de perguntas (padrão: .atlasbrain/benchmark.jsonl)")
    p.add_argument('--details', '--detalhes', dest='details', action="store_true", help="mostra o que veio nas perguntas erradas")
    p.add_argument("--json", help="salva as métricas nesse arquivo")
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("guard", help="(interno) lembrete do hook PreToolUse")
    p.set_defaults(fn=cmd_guard)

    p = sub.add_parser("init", help="transforma a pasta/projeto num cérebro (.atlasbrain/ + índice + git hooks)")
    p.add_argument('folder', nargs="?")
    p.add_argument('--no-git', '--sem-git', dest="no_git", action="store_true", help="não instala os git hooks")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("briefing", help="contexto curto para o início de sessão (usado pelo hook SessionStart)")
    _vault_arg(p)
    p.add_argument("--cwd", default="")
    p.set_defaults(fn=cmd_briefing)

    p = sub.add_parser("hooks", help="instala (ou --remove) a captura automática no Claude Code e no Codex")
    _vault_arg(p)
    p.add_argument("--remove", "--remover", action="store_true")
    p.set_defaults(fn=cmd_hooks)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
