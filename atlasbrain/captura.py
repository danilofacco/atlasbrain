"""Captura automática: hook do Claude Code / Codex que, ao fim de uma resposta ou sessão, lê o
transcript e registra no cérebro as decisões importantes e aprendizados que passaram batido.

O hook responde na hora (dispara um worker em segundo plano). O worker só age quando a conversa
cresceu o bastante desde a última captura e usa `claude -p` (Haiku) para decidir o que vale guardar.
Desligar: ATLASBRAIN_CAPTURA=0.
"""

import datetime as dt
import fcntl
import hashlib
from . import editor
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import data_dir
from .registro import registrar_aprendizado, registrar_decisao

MIN_NOVOS = int(os.environ.get("ATLASBRAIN_CAPTURA_MIN", "4000"))  # caracteres de conversa nova
INTERVALO = int(os.environ.get("ATLASBRAIN_CAPTURA_INTERVALO", "600"))  # segundos entre capturas da mesma sessão
JANELA = 40_000

PROMPT = """Você é o curador do "segundo cérebro" do usuário: uma pasta de notas markdown.
Leia o trecho de conversa abaixo (entre um usuário e um assistente de IA de programação) e extraia
SÓ o que merece virar nota permanente:

- "decisoes": escolhas importantes e duradouras que foram FECHADAS na conversa — arquitetura, stack,
  produto, negócio, prioridade, processo, preço. Não registre tarefas triviais, passos de execução,
  correções pontuais de bug, nem opções que só foram cogitadas.
- "aprendizados": descobertas não óbvias que valem para o futuro (uma pegadinha de uma ferramenta,
  um motivo técnico, um dado medido). Nada que qualquer documentação já diga.

Seja exigente: numa conversa comum o normal é 0 a 2 itens. Na dúvida, deixe de fora.
Escreva em português, com frases completas e específicas (nomes, números, o porquê).
Data da conversa: {hoje}. Converta datas relativas ("amanhã", "semana passada") em datas absolutas.
Notas que já existem no cérebro (não repita): {existentes}
Decisões ATIVAS no cérebro: {ativas}
Se uma decisão nova REVOGA ou MUDA uma dessas ativas, preencha "substitui" com o título exato dela.

Responda APENAS com JSON válido, sem markdown:
{{"decisoes": [{{"titulo": "...", "decisao": "...", "contexto": "...", "motivo": "...",
  "alternativas": ["..."], "consequencias": "...", "projeto": "nome do projeto ou null", "tags": ["..."],
  "substitui": "título de decisão ativa ou null", "importancia": 1-5, "durabilidade": 1-5}}],
 "aprendizados": [{{"titulo": "...", "conteudo": "...", "projeto": "... ou null", "tags": ["..."],
  "importancia": 1-5, "durabilidade": 1-5}}]}}
importancia: quanto isso muda o trabalho futuro (1 = detalhe, 5 = rumo do projeto).
durabilidade: por quanto tempo continua verdade (1 = só nesta sessão, 5 = meses).

Projeto/pasta de trabalho da conversa: {cwd}

=== CONVERSA ===
{conversa}
"""


def _log(vault: Path, msg: str) -> None:
    with open(data_dir(vault) / "captura.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def _textos(item, role=None):
    """Extrai (papel, texto) de uma linha de transcript do Claude Code ou do Codex, ignorando
    saídas de ferramenta (ruído) e mantendo só o que usuário e assistente disseram."""
    if isinstance(item, dict):
        r = item.get("role") or role
        t = item.get("type")
        if t in ("tool_result", "tool_use", "function_call", "function_call_output", "reasoning", "thinking"):
            return
        if isinstance(item.get("text"), str) and r in ("user", "assistant"):
            yield r, item["text"]
        for k, v in item.items():
            if k in ("text", "toolUseResult"):
                continue
            if isinstance(v, (dict, list)):
                yield from _textos(v, r)
            elif k == "content" and isinstance(v, str) and r in ("user", "assistant"):
                yield r, v
    elif isinstance(item, list):
        for x in item:
            yield from _textos(x, role)


def ler_conversa(transcript: Path) -> str:
    partes = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("isMeta") or obj.get("isSidechain"):
            continue
        for role, text in _textos(obj):
            text = text.strip()
            if not text or text.startswith("<"):  # system-reminder, environment_context, comandos…
                continue
            partes.append(f"[{'USUÁRIO' if role == 'user' else 'ASSISTENTE'}] {text}")
    return "\n\n".join(partes)


def _json_de(texto: str) -> dict:
    texto = texto[texto.find("{"): texto.rfind("}") + 1]
    return json.loads(texto)


def _extrair(prompt: str) -> dict:
    """Usa o CLI de IA que estiver logado: Claude (Haiku) primeiro, Codex (modelo leve) depois.
    ATLASBRAIN_CAPTURA_MOTOR=claude|codex força um deles."""
    env = {**os.environ, "ATLASBRAIN_CAPTURANDO": "1"}
    motor = os.environ.get("ATLASBRAIN_CAPTURA_MOTOR", "auto")
    erros = []
    if motor in ("auto", "claude"):
        try:
            r = subprocess.run(["claude", "-p", "--model", os.environ.get("ATLASBRAIN_CAPTURA_MODELO_CLAUDE", "haiku"),
                                "--strict-mcp-config", "--output-format", "text"],
                               input=prompt, capture_output=True, text=True, timeout=90, env=env)
            return _json_de(r.stdout)
        except Exception as e:
            erros.append(f"claude: {e}")
    if motor in ("auto", "codex"):
        try:
            r = subprocess.run(["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                                "-m", os.environ.get("ATLASBRAIN_CAPTURA_MODELO_CODEX", "gpt-5.6-luna"),
                                "-c", "model_reasoning_effort=low", "-"],
                               input=prompt, capture_output=True, text=True, timeout=240, env=env,
                               cwd=str(Path.home()))
            return _json_de(r.stdout)
        except Exception as e:
            erros.append(f"codex: {e}")
    raise RuntimeError("; ".join(erros) or "nenhum motor disponível")


def hook(vault_arg: str | None = None, evento: str | None = None) -> None:
    """Chamado pelo hook: lê o JSON do stdin e dispara o worker desacoplado. Nunca bloqueia nem falha.
    Sem --vault, o cérebro é o do projeto onde a sessão está (cwd do hook)."""
    if os.environ.get("ATLASBRAIN_CAPTURA", "1") == "0" or os.environ.get("ATLASBRAIN_CAPTURANDO"):
        return
    try:
        data = json.load(sys.stdin)
        from .config import resolve_vault
        vault = resolve_vault(vault_arg, cwd=data.get("cwd"))
    except Exception:
        return
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).exists():
        return
    evento = evento or data.get("hook_event_name") or "Stop"
    args = [sys.executable, "-m", "atlasbrain.cli", "capturar", "--vault", str(vault), "--worker",
            "--transcript", transcript, "--sessao", str(data.get("session_id", transcript)),
            "--cwd", str(data.get("cwd", "")), "--evento", evento]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env={**os.environ, "ATLASBRAIN_CAPTURANDO": "1"})


def _rotulo(tipo: str, titulo: str, res: dict) -> str:
    if res.get("criada"):
        extra = f" (substituiu {res['substituiu']})" if res.get("substituiu") else ""
        return f"{tipo} ✓ {res['path']} [rel {res.get('relevancia', 0):.2f}]{extra}"
    if res.get("atualizada"):
        return f"{tipo} ↻ atualizou {res['path']} (+{len(res['acrescentado'])} frase(s))"
    if res.get("path") is None and "relevância" in res.get("motivo", ""):
        return f"{tipo} ✗ recusada “{titulo}”: {res['motivo']}"
    return f"{tipo} = {res.get('path')} ({res.get('motivo', 'sem mudança')})"


def worker(vault: Path, transcript: Path, sessao: str, cwd: str, evento: str) -> None:
    # One extraction per vault; do not hold the Markdown writer lock during model calls.
    with open(data_dir(vault)/'capture.lock','a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        _worker(vault,transcript,sessao,cwd,evento)


def quality(vault):
    file=editor._cache(vault)/'capture-quality.json'
    return json.loads(file.read_text()) if file.exists() else {'contadores':{},'revisar':[]}


def _capture_result(vault, payload, action):
    key='capture-'+hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    result=editor.operation(vault,key,payload,action)
    with editor._lock(vault):
        report=quality(vault)
        seen=report.setdefault('operacoes',[])
        if key not in seen:
            category='revisar' if result.get('revisao_necessaria') else 'criadas' if result.get('criada') else 'atualizadas' if result.get('atualizada') else 'duplicadas' if result.get('path') else 'recusadas'
            counts=report.setdefault('contadores',{});counts[category]=counts.get(category,0)+1
            if category=='revisar':
                report.setdefault('revisar',[]).append({'operacao':key,'proposta':payload,'resultado':result})
                report['revisar']=report['revisar'][-100:]
            report['operacoes']=(seen+[key])[-10000:]
            editor._atomic(editor._cache(vault)/'capture-quality.json',json.dumps(report,ensure_ascii=False).encode(),mode=0o600)
    return result


def _worker(vault: Path, transcript: Path, sessao: str, cwd: str, evento: str) -> None:
    estado_f = data_dir(vault) / "captura.json"
    try:
        estado = json.loads(estado_f.read_text())
    except Exception:
        estado = {}
    s = estado.get(sessao, {"offset": 0, "quando": 0})
    conversa = ler_conversa(transcript)
    novos = conversa[s["offset"]:]
    final = evento == "SessionEnd"
    if len(novos) < (800 if final else MIN_NOVOS):
        return
    if not final and time.time() - s["quando"] < INTERVALO:
        return

    from .db import connect

    con = connect(vault)
    existentes = [r[0] for r in con.execute(
        "SELECT title FROM notes WHERE json_extract(frontmatter,'$.tipo') IN ('decisao','aprendizado') ORDER BY mtime DESC LIMIT 60")]
    ativas = [r[0] for r in con.execute(
        "SELECT title FROM notes WHERE json_extract(frontmatter, '$.tipo')='decisao' "
        "AND coalesce(json_extract(frontmatter, '$.status'), 'ativa')='ativa' ORDER BY mtime DESC LIMIT 40")]
    con.close()
    prompt = PROMPT.format(existentes="; ".join(existentes) or "nenhuma", ativas="; ".join(ativas) or "nenhuma",
                           hoje=dt.date.today().isoformat(), cwd=cwd or "?", conversa=novos[-JANELA:])
    try:
        itens = _extrair(prompt)
    except Exception as e:
        _log(vault, f"falha ao extrair ({sessao[:8]}): {e}")
        return

    origem = f"{'Codex' if '.codex' in str(transcript) else 'Claude Code'} · {Path(cwd).name if cwd else '?'}"
    feitos = []
    for d in itens.get("decisoes") or []:
        if d.get("titulo") and d.get("decisao"):
            res = _capture_result(vault, ['decisao',sessao,d], lambda: registrar_decisao(vault, d["titulo"], d["decisao"], d.get("contexto", ""), d.get("motivo", ""),
                                    d.get("alternativas"), d.get("consequencias", ""), d.get("projeto"),
                                    d.get("tags"), origem, substitui=d.get("substitui") or None, sessao=sessao,
                                    modo="automatico", llm=d))
            feitos.append(_rotulo("decisão", d["titulo"], res))
    for a in itens.get("aprendizados") or []:
        if a.get("titulo") and a.get("conteudo"):
            res = _capture_result(vault, ['aprendizado',sessao,a], lambda: registrar_aprendizado(vault, a["titulo"], a["conteudo"], a.get("projeto"), a.get("tags"), origem,
                                        sessao=sessao, modo="automatico", llm=a))
            feitos.append(_rotulo("aprendizado", a["titulo"], res))

    estado[sessao] = {"offset": len(conversa), "quando": time.time()}
    editor._atomic(estado_f,json.dumps(estado).encode(),mode=0o600)
    _log(vault, f"{evento} {sessao[:8]} ({len(novos)} chars novos): " + ("; ".join(feitos) or "nada relevante"))


def briefing(vault: Path, cwd: str = "", limite: int = 8) -> str:
    """Texto curto injetado no início de cada sessão do Claude/Codex: decisões ativas (primeiro as do
    projeto atual) e aprendizados recentes. Só lê o SQLite, então leva milissegundos."""
    from .db import connect

    con = connect(vault)
    projeto = Path(cwd).name.lower() if cwd else ""
    rows = con.execute(
        "SELECT title, path, json_extract(frontmatter,'$.tipo') tipo, json_extract(frontmatter,'$.data') data, "
        "coalesce(json_extract(frontmatter,'$.projeto'),'') projeto, coalesce(json_extract(frontmatter,'$.status'),'ativa') status "
        "FROM notes WHERE json_extract(frontmatter,'$.tipo') IN ('decisao','aprendizado') ORDER BY data DESC, mtime DESC LIMIT 200"
    ).fetchall()
    con.close()
    dec = [r for r in rows if r["tipo"] == "decisao" and r["status"] == "ativa"]
    apr = [r for r in rows if r["tipo"] == "aprendizado"]
    daqui = lambda r: projeto and projeto in r["projeto"].lower()
    dec.sort(key=lambda r: not daqui(r))
    apr.sort(key=lambda r: not daqui(r))
    fmt = lambda r: f"- {r['data']} {r['title']}" + (f" ({r['projeto'].strip('[]')})" if r["projeto"] else "")
    out = ["[Segundo cérebro] Contexto registrado em conversas anteriores (use o MCP atlasbrain: "
           "`ler` para detalhes, `buscar` para o resto; registre decisões novas com `registrar_decisao`)."]
    if dec:
        out.append("Decisões ativas:\n" + "\n".join(fmt(r) for r in dec[:limite]))
    if apr:
        out.append("Aprendizados recentes:\n" + "\n".join(fmt(r) for r in apr[:max(3, limite // 2)]))
    if not dec and not apr:
        out.append("Ainda não há decisões registradas: registre as importantes que surgirem nesta conversa.")
    return "\n".join(out)[:4000]
