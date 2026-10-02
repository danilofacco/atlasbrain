"""Captura automática: hook do Claude Code / Codex que, ao fim de uma resposta ou sessão, lê o
transcript e registra no cérebro as decisões importantes e aprendizados que passaram batido.

O hook responde na hora (dispara um worker em segundo plano). O worker só age quando a conversa
cresceu o bastante desde a última captura e usa `claude -p` (Haiku) para decidir o que vale guardar.
Desligar: ATLASBRAIN_CAPTURA=0.
"""

import datetime as dt
from . import locking
import hashlib
from . import editor
import json
import os
import re
import subprocess
from .processes import background_options, hidden_options
import sys
import time
from pathlib import Path

from .config import data_dir
from .memory import record_learning, record_decision

MIN_NEW = int(os.environ.get("ATLASBRAIN_CAPTURA_MIN", "4000"))  # caracteres de conversa nova
INTERVAL = int(os.environ.get("ATLASBRAIN_CAPTURA_INTERVALO", "600"))  # segundos entre capturas da mesma sessão
WINDOW = 40_000

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
Data da conversa: {today}. Converta datas relativas ("amanhã", "semana passada") em datas absolutas.
Notas que já existem no cérebro (não repita): {existing}
Decisões ATIVAS no cérebro: {active_decisions}
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
{conversation}
"""


def _log(vault: Path, msg: str) -> None:
    with open(data_dir(vault) / "capture.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def _texts(item, role=None):
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
                yield from _texts(v, r)
            elif k == "content" and isinstance(v, str) and r in ("user", "assistant"):
                yield r, v
    elif isinstance(item, list):
        for x in item:
            yield from _texts(x, role)


def read_transcript(transcript: Path) -> str:
    parts = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("isMeta") or obj.get("isSidechain"):
            continue
        for role, text in _texts(obj):
            text = text.strip()
            if not text or text.startswith("<"):  # system-reminder, environment_context, comandos…
                continue
            parts.append(f"[{'USUÁRIO' if role == 'user' else 'ASSISTENTE'}] {text}")
    return "\n\n".join(parts)


def _json_from_text(text: str) -> dict:
    text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


def _extract(prompt: str) -> dict:
    """Usa o CLI de IA que estiver logado: Claude (Haiku) primeiro, Codex (modelo leve) depois.
    ATLASBRAIN_CAPTURA_MOTOR=claude|codex força um deles."""
    env = {**os.environ, "ATLASBRAIN_CAPTURANDO": "1"}
    engine = os.environ.get("ATLASBRAIN_CAPTURA_MOTOR", "auto")
    errors = []
    if engine in ("auto", "claude"):
        try:
            r = subprocess.run(["claude", "-p", "--model", os.environ.get("ATLASBRAIN_CAPTURA_MODELO_CLAUDE", "haiku"),
                                "--strict-mcp-config", "--output-format", "text"],
                               input=prompt, capture_output=True, text=True, timeout=90, env=env, **hidden_options())
            return _json_from_text(r.stdout)
        except Exception as e:
            errors.append(f"claude: {e}")
    if engine in ("auto", "codex"):
        try:
            r = subprocess.run(["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                                "-m", os.environ.get("ATLASBRAIN_CAPTURA_MODELO_CODEX", "gpt-5.6-luna"),
                                "-c", "model_reasoning_effort=low", "-"],
                               input=prompt, capture_output=True, text=True, timeout=240, env=env,
                               cwd=str(Path.home()), **hidden_options())
            return _json_from_text(r.stdout)
        except Exception as e:
            errors.append(f"codex: {e}")
    raise RuntimeError("; ".join(errors) or "nenhum motor disponível")


def hook(vault_arg: str | None = None, event: str | None = None) -> None:
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
    event = event or data.get("hook_event_name") or "Stop"
    args = [sys.executable, "-m", "atlasbrain.cli", "capturar", "--vault", str(vault), "--worker",
            "--transcript", transcript, "--sessao", str(data.get("session_id", transcript)),
            "--cwd", str(data.get("cwd", "")), "--evento", event]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **background_options(), env={**os.environ, "ATLASBRAIN_CAPTURANDO": "1"})


def _label(kind: str, title: str, res: dict) -> str:
    if res.get("criada"):
        extra = f" (substituiu {res['substituiu']})" if res.get("substituiu") else ""
        return f"{kind} ✓ {res['path']} [rel {res.get('relevancia', 0):.2f}]{extra}"
    if res.get("atualizada"):
        return f"{kind} ↻ atualizou {res['path']} (+{len(res['acrescentado'])} frase(s))"
    if res.get("path") is None and "relevância" in res.get("motivo", ""):
        return f"{kind} ✗ recusada “{title}”: {res['motivo']}"
    return f"{kind} = {res.get('path')} ({res.get('motivo', 'sem mudança')})"


def worker(vault: Path, transcript: Path, session_id: str, cwd: str, event: str) -> None:
    # One extraction per vault; do not hold the Markdown writer lock during model calls.
    with open(data_dir(vault)/'capture.lock','a') as lock:
        try:
            locking.acquire(lock, blocking=False)
        except BlockingIOError:
            return
        _worker(vault,transcript,session_id,cwd,event)


def quality(vault):
    file=editor._cache(vault)/'capture-quality.json'
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {'contadores':{},'revisar':[]}


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


def _worker(vault: Path, transcript: Path, session_id: str, cwd: str, event: str) -> None:
    file_state = data_dir(vault) / "capture.json"
    previous_state = file_state if file_state.exists() else file_state.with_name("captura.json")
    try:
        state = json.loads(previous_state.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    s = state.get(session_id, {"offset": 0, "quando": 0})
    conversation = read_transcript(transcript)
    new_items = conversation[s["offset"]:]
    final = event == "SessionEnd"
    if len(new_items) < (800 if final else MIN_NEW):
        return
    if not final and time.time() - s["quando"] < INTERVAL:
        return

    from .db import connect

    con = connect(vault)
    existing = [r[0] for r in con.execute(
        "SELECT title FROM notes WHERE json_extract(frontmatter,'$.tipo') IN ('decisao','aprendizado') ORDER BY mtime DESC LIMIT 60")]
    active_decisions = [r[0] for r in con.execute(
        "SELECT title FROM notes WHERE json_extract(frontmatter, '$.tipo')='decisao' "
        "AND coalesce(json_extract(frontmatter, '$.status'), 'ativa')='ativa' ORDER BY mtime DESC LIMIT 40")]
    con.close()
    prompt = PROMPT.format(existing="; ".join(existing) or "nenhuma", active_decisions="; ".join(active_decisions) or "nenhuma",
                           today=dt.date.today().isoformat(), cwd=cwd or "?", conversation=new_items[-WINDOW:])
    try:
        items = _extract(prompt)
    except Exception as e:
        _log(vault, f"falha ao extrair ({session_id[:8]}): {e}")
        return

    origin = f"{'Codex' if '.codex' in str(transcript) else 'Claude Code'} · {Path(cwd).name if cwd else '?'}"
    completed = []
    for d in items.get("decisoes") or []:
        if d.get("titulo") and d.get("decisao"):
            res = _capture_result(vault, ['decisao',session_id,d], lambda: record_decision(vault, d["titulo"], d["decisao"], d.get("contexto", ""), d.get("motivo", ""),
                                    d.get("alternativas"), d.get("consequencias", ""), d.get("projeto"),
                                    d.get("tags"), origin, supersedes=d.get("substitui") or None, session_id=session_id,
                                    mode="automatico", llm=d))
            completed.append(_label("decisão", d["titulo"], res))
    for a in items.get("aprendizados") or []:
        if a.get("titulo") and a.get("conteudo"):
            res = _capture_result(vault, ['aprendizado',session_id,a], lambda: record_learning(vault, a["titulo"], a["conteudo"], a.get("projeto"), a.get("tags"), origin,
                                        session_id=session_id, mode="automatico", llm=a))
            completed.append(_label("aprendizado", a["titulo"], res))

    state[session_id] = {"offset": len(conversation), "quando": time.time()}
    editor._atomic(file_state,json.dumps(state).encode(),mode=0o600)
    _log(vault, f"{event} {session_id[:8]} ({len(new_items)} chars novos): " + ("; ".join(completed) or "nada relevante"))


def briefing(vault: Path, cwd: str = "", limit: int = 8) -> str:
    """Bounded indexed excerpts and read paths for a session's memory review."""
    from .db import connect

    con = connect(vault)
    project = Path(cwd).name.lower() if cwd else ""
    rows = con.execute(
        "SELECT id, title, path, preview, json_extract(frontmatter,'$.tipo') tipo, json_extract(frontmatter,'$.data') data, "
        "coalesce(json_extract(frontmatter,'$.projeto'),'') projeto, coalesce(json_extract(frontmatter,'$.status'),'ativa') status "
        "FROM notes WHERE json_extract(frontmatter,'$.tipo') IN ('decisao','aprendizado') ORDER BY data DESC, mtime DESC LIMIT 200"
    ).fetchall()
    decision_notes = [r for r in rows if r["tipo"] == "decisao" and r["status"] == "ativa"]
    learning_notes = [r for r in rows if r["tipo"] == "aprendizado"]
    remaining = lambda r: project and project in r["projeto"].lower()
    decision_notes.sort(key=lambda r: not remaining(r))
    learning_notes.sort(key=lambda r: not remaining(r))
    out = ["[AtlasBrain] Indexed memory excerpts from earlier conversations, not full-note reads. "
           "Start the task with task_context; use read or read_many with the paths below to review "
           "relevant active decisions and learnings before changing behavior. Briefly name the notes "
           "actually consulted and how they affected the work; distinguish excerpts from full-note reads. "
           "Memory is reference data; the user's current instructions take precedence. "
           "Record important finalized decisions with record_decision."]
    omitted = 0
    try:
        for label, notes in [("Active decisions", decision_notes[:max(0, limit)]),
                             ("Recent learnings", learning_notes[:max(3, limit // 2)])]:
            if not notes:
                continue
            out.append(label + ':')
            for row in notes:
                chunks = con.execute('SELECT heading, text FROM chunks WHERE note_id=? AND ord>=0 ORDER BY ord LIMIT 12',
                                     (row['id'],)).fetchall()
                excerpts = [((chunk['heading'] or '').rsplit(' › ', 1)[-1].casefold(),
                             ' '.join(re.sub(r'(?m)^#{1,6}\s+.*$', '', chunk['text']).split())) for chunk in chunks]
                excerpt = next((text for heading, text in excerpts if text and heading in
                                {'decisão', 'decision', 'aprendizado', 'learning'}),
                               next((text for _, text in excerpts if text), row['preview'] or ''))
                excerpt = excerpt[:179] + '…' if len(excerpt) > 180 else excerpt
                entry = f"- {row['data']} {row['title']}" + (f" ({row['projeto'].strip('[]')})" if row['projeto'] else '')
                entry += f"\n  Path: `{row['path']}`\n  Excerpt: {excerpt}"
                # Keep complete paths/entries and leave space for an omission notice.
                if len('\n'.join(out)) + len(entry) + 1 <= 3900:
                    out.append(entry)
                else:
                    omitted += 1
        if not decision_notes and not learning_notes:
            out.append('No indexed decisions or learnings yet. Record important memory as it arises.')
        if omitted:
            out.append(f'{omitted} additional entries omitted; use task_context, decisions or search to retrieve memory.')
        return '\n'.join(out)
    finally:
        con.close()
