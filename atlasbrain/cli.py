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

    stats = index_vault(resolve_vault(a.vault), force=a.force, quiet=a.silencioso,
                        esperar=0 if a.silencioso else 900)  # na mão: espera o ciclo automático; git hook: não
    if not a.silencioso:
        print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_search(a):
    from .db import connect
    from .search import Searcher

    res = Searcher(connect(resolve_vault(a.vault))).search(" ".join(a.consulta), limit=a.limite, tag=a.tag)
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
            service.run_daemon(resolve_vault(a.vault), a.porta, not a.sem_auto)
        else:
            vault = resolve_vault(a.vault)
            from .config import pasta_invalida, GLOBAL_BRAIN
            if vault != GLOBAL_BRAIN.resolve() and (reason := pasta_invalida(vault)):
                raise RuntimeError(reason)
            live = service.start(vault, a.porta, not a.sem_auto)
            if a.cmd == 'setup':
                print(service.client_config(vault, a.client, a.porta, a.nome), end='')
                print(f"Service ready: PID {live['pid']}, port {live['port']}. Paste the configuration into your client.", file=sys.stderr)
            else:
                print(json.dumps(live, indent=2))
                if a.cmd == 'serve' and not a.nao_abrir:
                    import webbrowser
                    from urllib.parse import urlencode
                    webbrowser.open(f"http://127.0.0.1:{a.porta}/?" + urlencode({'v': str(vault)}))
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


def cmd_grafo(a):
    import threading
    from types import SimpleNamespace
    from .db import connect
    from .search import Searcher
    from . import ferramentas as f

    vault = resolve_vault(a.vault)
    con = connect(vault)
    ctx = SimpleNamespace(vault=vault, con=con, db_lock=threading.Lock(), searcher=Searcher(con))
    try:
        if a.operacao == 'consultar':
            result = f.consultar_grafo(ctx, ' '.join(a.consulta), modo=a.modo, profundidade=a.profundidade,
                                      tokens=a.tokens, direcao=a.direcao, relacoes=a.relacao)
        elif a.operacao == 'impacto':
            result = f.impacto(ctx, a.alvo, profundidade=a.profundidade, tokens=a.tokens,
                               incluir_inferidas=a.incluir_inferidas)
        else:
            result = f.caminho(ctx, a.de, a.ate, direcao=a.direcao, tokens=a.tokens)
        print(result)
    finally:
        con.close()


def cmd_mcp(a):
    from .mcp_server import run

    run(resolve_vault(a.vault), auto_index=not a.sem_auto)


def cmd_import(a):
    from .importacao import importar
    from .indexer import index_vault
    vault = resolve_vault(a.vault)
    try:
        result = importar(vault, a.url, a.titulo, a.atualizar)
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
        targets.append(('claude', ['mcp', 'add', '--transport', 'http', '--scope', 'user', a.nome, url]))
    if a.codex or not (a.claude or a.codex):
        targets.append(('codex', ['mcp', 'add', a.nome, '--url', url]))
    for tool, args in targets:
        if not shutil.which(tool):
            print(f'{tool}: command not found; use setup to generate configuration.')
            continue
        result = subprocess.run([tool, *args], capture_output=True, text=True)
        print((result.stdout + result.stderr).strip())
        if result.returncode:
            raise SystemExit(result.returncode)


def cmd_capture(a):
    from . import captura

    if not a.worker and not a.transcript:
        captura.hook(a.vault, a.evento)  # o projeto sai do cwd que vem no JSON do hook
        return
    vault = resolve_vault(a.vault, cwd=a.cwd or None)
    if a.worker:
        captura.worker(vault, Path(a.transcript), a.sessao, a.cwd, a.evento)
    elif a.transcript:  # manual: extrai de um transcript específico agora
        captura.worker(vault, Path(a.transcript), a.sessao or a.transcript, a.cwd or "", "SessionEnd")


def cmd_briefing(a):
    from .captura import briefing

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

BLOCO_INICIO, BLOCO_FIM = "<!-- atlasbrain:inicio -->", "<!-- atlasbrain:fim -->"
BLOCO = f"""{BLOCO_INICIO}
## AtlasBrain — project second brain
Each project has a local `.atlasbrain/` brain (code graph, decisions and learnings), exposed by the `atlasbrain` MCP.
- Before grep/glob, use `find_files` (names/symbols), `symbol_location` (definitions/usages),
  `explain` (dependencies and rationale), `graph_path` and `report`.
- For task context and history: `task_context`, `search`, `decisions`, `read` and `related`.
- INFERRED relations are clues; EXTRACTED relations have code/text evidence.
- Record important finalized decisions with `record_decision` (`supersedes` replaces an earlier decision);
  record non-obvious discoveries with `record_learning`.
- On a global endpoint, pass `project_folder` with the absolute folder where you are working.
{BLOCO_FIM}
"""


def _bloco_instrucoes(arquivo: Path, remover: bool) -> None:
    txt = arquivo.read_text() if arquivo.exists() else ""
    if BLOCO_INICIO in txt:
        a, b = txt.index(BLOCO_INICIO), txt.index(BLOCO_FIM) + len(BLOCO_FIM)
        txt = (txt[:a].rstrip() + "\n" + txt[b:].lstrip("\n")).strip() + "\n"
    if not remover:
        txt = txt.rstrip() + ("\n\n" if txt.strip() else "") + BLOCO
    arquivo.write_text(txt)


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
        proj = find_project(Path(data.get("cwd") or "."))
        if not proj or not (proj / BRAIN).is_dir():
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
            sid = str(proj) + ":" + str(data.get("session_id", "?"))
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
    vault = Path(a.pasta).expanduser().resolve() if a.pasta else resolve_vault()
    from .config import BRAIN, data_dir
    data_dir(vault)
    print(f"✓ cérebro em {vault / BRAIN}")
    hooks_dir = vault / ".git" / "hooks"
    if hooks_dir.is_dir() and not a.sem_git:
        exe = shutil.which("atlasbrain") or str(Path(sys.argv[0]).resolve())
        linha = f'"{exe}" index --vault "{vault}" --silencioso >/dev/null 2>&1 &  # atlasbrain'
        for h in ("post-commit", "post-checkout", "post-merge"):
            f = hooks_dir / h
            txt = f.read_text() if f.exists() else "#!/bin/sh\n"
            if "# atlasbrain" not in txt:
                f.write_text(txt.rstrip() + "\n" + linha + "\n")
                f.chmod(0o755)
        print("✓ git hooks: reindexa em segundo plano a cada commit, checkout e merge")
    from .indexer import index_vault
    print(json.dumps(index_vault(vault), ensure_ascii=False))


def cmd_bench(a):
    from . import bench
    from .config import BRAIN
    vault = resolve_vault(a.vault)
    f = Path(a.arquivo) if a.arquivo else vault / BRAIN / "benchmark.jsonl"
    if not f.exists():
        raise SystemExit(f"Sem perguntas em {f}. Formato: uma linha JSON por pergunta "
                         '{"pergunta": "...", "esperado": ["caminho/arquivo"], "tipo": "conceito"}')
    res = bench.rodar(vault, bench.carregar(f))
    print(bench.relatorio(res, detalhes=a.detalhes))
    if a.json:
        Path(a.json).write_text(json.dumps({k: bench.metricas(v) for k, v in res.items()}, ensure_ascii=False, indent=2))


def _remove_owned_hooks(hooks):
    kept = []
    for entry in hooks:
        handlers = [h for h in entry.get("hooks", []) if not (
            "atlasbrain" in h.get("command", "") and
            any(k in h.get("command", "") for k in ("capturar", "briefing", " guard")))]
        if handlers:
            kept.append({**entry, "hooks": handlers})
    return kept


def _merge_hook(settings: Path, evento: str, command: str) -> bool:
    data = json.loads(settings.read_text()) if settings.exists() else {}
    hooks = data.setdefault("hooks", {}).setdefault(evento, [])
    hooks[:] = _remove_owned_hooks(hooks)
    entry = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    if evento == "PreToolUse":
        entry["matcher"] = "Bash|Read|Grep|Glob"
    hooks.append(entry)
    settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def cmd_hooks(a):
    exe = shutil.which("atlasbrain") or str(Path(sys.argv[0]).resolve())
    fixo = f' --vault "{resolve_vault(a.vault)}"' if a.vault else ""  # sem --vault: cérebro do projeto da sessão
    cmd = f'"{exe}" capturar{fixo}'
    brief = f'"{exe}" briefing{fixo}'
    guard = f'"{exe}" guard'
    targets = [(Path.home() / ".claude" / "settings.json", ["Stop", "SessionEnd", "SessionStart", "PreToolUse"]),
               (Path.home() / ".codex" / "hooks.json", ["Stop", "SessionStart", "PreToolUse"])]
    for f, eventos in targets:
        if not f.parent.exists():
            continue
        for ev in eventos:
            c = brief if ev == "SessionStart" else guard if ev == "PreToolUse" else cmd
            if a.remover:
                data = json.loads(f.read_text()) if f.exists() else {}
                lst = data.get("hooks", {}).get(ev, [])
                lst[:] = _remove_owned_hooks(lst)
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            else:
                _merge_hook(f, ev, c)
        print(f"{'removido de' if a.remover else '✓ hook em'} {f} ({', '.join(eventos)})")
    for f in (Path.home() / ".claude" / "CLAUDE.md", Path.home() / ".codex" / "AGENTS.md"):
        if f.parent.exists():
            _bloco_instrucoes(f, a.remover)
            print(f"{'removido de' if a.remover else '✓ instruções em'} {f}")


def main():
    ap = argparse.ArgumentParser(prog=Path(sys.argv[0]).name if Path(sys.argv[0]).name in ("atlasbrain",) else "atlasbrain", description="Segundo cérebro local sobre uma pasta.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for command in ('setup', 'start', '_daemon'):
        p = sub.add_parser(command, help='shared local HTTP service' if command != '_daemon' else argparse.SUPPRESS)
        _vault_arg(p)
        p.add_argument('--porta', type=int, default=8765)
        p.add_argument('--sem-auto', action='store_true')
        if command == 'setup':
            p.add_argument('--client', choices=['claude', 'antigravity', 'codex'], required=True)
            p.add_argument('--nome', default='atlasbrain')
        p.set_defaults(fn=cmd_service)
    for command in ('status', 'stop'):
        p = sub.add_parser(command, help='inspect/stop the shared service')
        p.set_defaults(fn=cmd_service)

    p = sub.add_parser('update', help='check, apply or configure version-gated updates')
    p.add_argument('action', choices=('status', 'check', 'apply', 'enable', 'disable'))
    p.add_argument('--vault', help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser('importar-url', help='import a public web page or PDF')
    _vault_arg(p)
    p.add_argument('url')
    p.add_argument('--titulo')
    p.add_argument('--atualizar', action='store_true')
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("index", help="indexa (incremental) a pasta")
    _vault_arg(p)
    p.add_argument("--force", action="store_true", help="reprocessa tudo")
    p.add_argument("--silencioso", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("search", aliases=["buscar"], help="busca híbrida no terminal")
    _vault_arg(p)
    p.add_argument("consulta", nargs="+")
    p.add_argument("--limite", "-n", type=int, default=10)
    p.add_argument("--tag")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser('consultar', help='busca e percorre o grafo de arquivos/símbolos')
    _vault_arg(p)
    p.add_argument('consulta', nargs='+')
    p.add_argument('--modo', choices=['bfs', 'dfs'], default='bfs')
    p.add_argument('--profundidade', type=int, choices=range(7), default=2)
    p.add_argument('--direcao', choices=['entrada', 'saida', 'ambas'], default='ambas')
    p.add_argument('--relacao', action='append', help='filtra relações; pode repetir')
    p.add_argument('--tokens', type=int, default=2000)
    p.set_defaults(fn=cmd_grafo, operacao='consultar')

    p = sub.add_parser('impacto', help='dependentes afetados por uma alteração')
    _vault_arg(p)
    p.add_argument('alvo')
    p.add_argument('--profundidade', type=int, choices=range(7), default=3)
    p.add_argument('--incluir-inferidas', action='store_true')
    p.add_argument('--tokens', type=int, default=2000)
    p.set_defaults(fn=cmd_grafo, operacao='impacto')

    p = sub.add_parser('caminho', help='caminho entre arquivos ou símbolos')
    _vault_arg(p)
    p.add_argument('de')
    p.add_argument('ate')
    p.add_argument('--direcao', choices=['entrada', 'saida', 'ambas'], default='ambas')
    p.add_argument('--tokens', type=int, default=2000)
    p.set_defaults(fn=cmd_grafo, operacao='caminho')

    p = sub.add_parser("serve", help="abre a interface web com o grafo")
    _vault_arg(p)
    p.add_argument("--porta", type=int, default=8765)
    p.add_argument("--sem-auto", action="store_true", help="não reindexa automaticamente")
    p.add_argument("--nao-abrir", action="store_true", help="não abre o navegador")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("mcp", help="servidor MCP (stdio) para Claude/Codex")
    _vault_arg(p)
    p.add_argument("--sem-auto", action="store_true")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("stats", help="números do índice")
    _vault_arg(p)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("conectar", help="registra o MCP no Claude Code e no Codex")
    _vault_arg(p)
    p.add_argument("--claude", action="store_true")
    p.add_argument("--codex", action="store_true")
    p.add_argument("--nome", default="atlasbrain")
    p.set_defaults(fn=cmd_connect)

    p = sub.add_parser("capturar", help="captura decisões/aprendizados de um transcript (usado pelos hooks)")
    _vault_arg(p)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--transcript")
    p.add_argument("--sessao", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--evento")
    p.set_defaults(fn=cmd_capture)

    p = sub.add_parser("bench", help="mede a qualidade da busca com perguntas de resposta conhecida")
    _vault_arg(p)
    p.add_argument("--arquivo", help="JSONL de perguntas (padrão: .atlasbrain/benchmark.jsonl)")
    p.add_argument("--detalhes", action="store_true", help="mostra o que veio nas perguntas erradas")
    p.add_argument("--json", help="salva as métricas nesse arquivo")
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("guard", help="(interno) lembrete do hook PreToolUse")
    p.set_defaults(fn=cmd_guard)

    p = sub.add_parser("init", help="transforma a pasta/projeto num cérebro (.atlasbrain/ + índice + git hooks)")
    p.add_argument("pasta", nargs="?")
    p.add_argument("--sem-git", action="store_true", help="não instala os git hooks")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("briefing", help="contexto curto para o início de sessão (usado pelo hook SessionStart)")
    _vault_arg(p)
    p.add_argument("--cwd", default="")
    p.set_defaults(fn=cmd_briefing)

    p = sub.add_parser("hooks", help="instala (ou --remover) a captura automática no Claude Code e no Codex")
    _vault_arg(p)
    p.add_argument("--remover", action="store_true")
    p.set_defaults(fn=cmd_hooks)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
