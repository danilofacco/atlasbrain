"""Escrita estruturada no cérebro: decisões e aprendizados viram markdown com frontmatter,
ligados ao projeto por [[wikilink]] e sem duplicar o que já foi registrado."""

import datetime as dt
import json
import re
from pathlib import Path

import numpy as np

from . import embed, editor
from .config import notes_dir
from .relevance import evaluate
from .db import connect, from_blob
from .indexer import index_vault

MEMORY_FOLDERS = {"decisao": "Decisions", "aprendizado": "Learnings"}
DUPLICATE_THRESHOLD = 0.85
SIMILAR_THRESHOLD = 0.72  # abaixo de duplicata, mas talvez uma mudança da mesma decisão


def _slug(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "", title).strip()[:90] or "Sem título"


def _yaml(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _project_link(con, project: str | None) -> str | None:
    if not project:
        return None
    name = project.strip().strip("[]")
    row = con.execute(
        "SELECT title FROM notes WHERE lower(title)=? OR lower(path) LIKE ? ORDER BY length(path) LIMIT 1",
        (name.lower(), f"%/{name.lower()}.md"),
    ).fetchone()
    return f"[[{row['title'] if row else name}]]"


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.search(r"^(.{15,}?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:300]


def core_subject(title: str, text: str) -> str:
    """O assunto de uma nota: título + primeira frase. Detalhe novo não muda o núcleo, então uma nota
    com mais informação continua sendo reconhecida como o mesmo assunto (e vira atualização)."""
    return f"{title}. {_first_sentence(text)}"


def _essence(vault: Path, row) -> str:
    """Núcleo de uma nota já gravada: título + primeira frase da seção Decisão (ou do corpo)."""
    try:
        text = (vault / row["path"]).read_text(encoding="utf-8")
    except OSError:
        return row["title"]
    m = re.search(r"## Decisão\s+(.+?)(?:\n## |\Z)", text, re.S)
    body = m.group(1) if m else re.sub(r"^---.*?---\s*(# .*?\n)?", "", text, flags=re.S)
    return core_subject(row["title"], body)


_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")


def has_conflict(previous_core: str, new_core: str) -> bool:
    """Mesmo assunto com valores diferentes (R$ 97 → R$ 147, 3 → 5 réplicas): é uma MUDANÇA, não um detalhe."""
    a, b = set(_NUMBERS.findall(previous_core)), set(_NUMBERS.findall(new_core))
    return bool(a and b and a != b)


def _similar_note(vault: Path, con, kind: str, text: str):
    """Maior similaridade entre o texto novo e as notas ativas do mesmo tipo (título + miolo contra
    título + miolo). Medido: duplicatas 0,88–0,91; decisões diferentes 0,65–0,76."""
    rows = [
        r for r in con.execute("SELECT path, title, frontmatter FROM notes")
        if (fm := json.loads(r["frontmatter"] or "{}")).get("tipo") == kind and fm.get("status", "ativa") == "ativa"
    ]
    if not rows:
        return None
    cores = [_essence(vault, r) for r in rows]
    normalized = lambda value: ' '.join(value.casefold().split())
    for row, core in zip(rows, cores):
        if normalized(core)==normalized(text):
            return row, 1.0, core
    if not embed.enabled():
        return None
    V = embed.embed([text] + cores)
    scores = V[1:] @ V[0]
    i = int(np.argmax(scores))
    return rows[i], float(scores[i]), cores[i]


_PHRASE = re.compile(r"(?<=[.!?])\s+|\n+(?:[-*]\s+)?")
_CONCRETE = re.compile(r"\b\d+(?:[.,]\d+)?\b|[\w.-]+/[\w./-]+|`[^`]+`|\b[a-z]+[A-Z]\w*\b|\b\w+_\w+\b|R\$\s?\d+")


def _sentences(text: str) -> list[str]:
    text = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
    text = re.sub(r"^#+ .*$", "", text, flags=re.M)
    return [f.strip(" -*") for f in _PHRASE.split(text) if len(f.strip(" -*")) >= 15]


def new_information(previous_text: str, new_text: str, threshold: float = 0.8) -> list[str]:
    """Frases do texto novo que a nota antiga ainda não tem. Uma frase é nova se traz algo concreto
    (número, arquivo, identificador) ausente da nota, ou se o sentido dela não está em nenhuma frase antiga."""
    previous_notes, new_notes = _sentences(previous_text), _sentences(new_text)
    if not new_notes:
        return []
    known = {m.lower() for m in _CONCRETE.findall(previous_text)}
    candidates = []
    for f in new_notes:
        concrete_signals = {m.lower() for m in _CONCRETE.findall(f)}
        if concrete_signals - known:
            candidates.append((f, True))  # número/arquivo/nome que a nota não tinha: é novidade com certeza
        else:
            candidates.append((f, False))
    uncertain = [f for f, correct in candidates if not correct]
    new_by_direction = set()
    if uncertain and previous_notes and embed.enabled():
        V = embed.embed(uncertain + previous_notes)
        sim = V[: len(uncertain)] @ V[len(uncertain):].T
        new_by_direction = {uncertain[i] for i in range(len(uncertain)) if sim[i].max() < threshold}
    elif uncertain and not previous_notes:
        new_by_direction = set(uncertain)
    elif uncertain:  # sem embeddings: compara palavras
        previous_words = set(re.findall(r"\w{4,}", previous_text.lower()))
        for f in uncertain:
            word = set(re.findall(r"\w{4,}", f.lower()))
            if word and len(word - previous_words) / len(word) > 0.5:
                new_by_direction.add(f)
    return [f for f, correct in candidates if correct or f in new_by_direction]


@editor.serialized
def update_note(vault: Path, target: str, text: str, origin: str | None = None, session_id: str | None = None,
                   only_new: bool = True, revision: str | None = None) -> dict:
    """Acrescenta a uma nota existente só o que é novo, numa seção datada "## Atualizações".
    Nunca apaga nem reescreve o que já estava lá: o histórico fica legível."""
    from .graph import find_note
    con = connect(vault)
    try:
        row = find_note(con, target)
    finally:
        con.close()
    if row is None:
        return {"atualizada": False, "path": None, "motivo": f"Nota não encontrada: '{target}'."}
    path = vault / row["path"]
    if row["kind"] != "nota" or not path.exists():
        return {"atualizada": False, "path": row["path"], "motivo": "Só dá para atualizar notas markdown."}
    snapshot = editor.read(vault, row['path'])
    current = snapshot['content']
    if revision and revision != snapshot['revision']:
        raise editor.EditError('A nota mudou. Leia novamente antes de atualizar.', 409)
    new_notes = new_information(current, text) if only_new else _sentences(text) or [text.strip()]
    if not new_notes:
        return {"atualizada": False, "path": row["path"], "motivo": "Nada novo: a nota já diz isso."}
    today = dt.date.today().isoformat()
    author = f" ({origin})" if origin else ""
    block = f"- **{today}**{author}: " + " ".join(n.rstrip(".") + "." for n in new_notes)
    if "\n## Atualizações" in current:
        body = current.rstrip() + "\n" + block + "\n"
    else:
        body = current.rstrip() + "\n\n## Atualizações\n\n" + block + "\n"
    from .parse import split_frontmatter
    fm = split_frontmatter(body)[0]
    extra = {"atualizado": today, "atualizacoes": int(fm.get("atualizacoes", 0) or 0) + 1}
    if session_id:
        extra["sessoes"] = sorted({*(fm.get("sessoes") or []), *( [fm["sessao"]] if fm.get("sessao") else []), session_id})
    editor.save(vault, row['path'], _frontmatter(body, extra), snapshot['revision'])
    index_vault(vault, quiet=True)
    return {"atualizada": True, "path": row["path"], "acrescentado": new_notes}


def _frontmatter(text: str, updates: dict) -> str:
    from .parse import split_frontmatter
    fm, body = split_frontmatter(text)
    fm.update(updates)
    return "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in fm.items()) + "\n---\n" + body


def _set_frontmatter(vault: Path, path: Path, updates: dict) -> None:
    snapshot = editor.read(vault, path.relative_to(vault).as_posix())
    editor.save(vault, snapshot['path'], _frontmatter(snapshot['content'], updates), snapshot['revision'])


@editor.serialized
def _write_memory(vault: Path, kind: str, title: str, fm: dict, body: str, dedupe_text: str, force: bool,
              supersedes: str | None = None, mode: str = "manual", reason: str = "", llm: dict | None = None) -> dict:
    """modo="manual": o agente pediu (portão frouxo, só barra o claramente inútil).
    modo="automatico": a captura decidiu sozinha (portão exigente)."""
    content = re.sub(r"^## .*$", "", body, flags=re.M)  # avalia o que foi dito, não os cabeçalhos do modelo
    av = evaluate(kind, title, content, reason, mode=mode, llm=llm)
    if not av.accepted and not force:
        return {"criada": False, "path": None, "relevancia": av.score,
                "motivo": f"Não parece valer uma nota ({av.summary()}). Se vale, complete ou use forcar=True."}
    fm["relevancia"] = av.score
    index_vault(vault, quiet=True)
    con = connect(vault)
    previous = None
    try:
        if supersedes and mode == 'automatico':
            return {'criada':False,'path':None,'revisao_necessaria':True,'candidata':supersedes,
                    'motivo':'A captura propôs uma substituição; confirme pelo registro manual.'}
        if supersedes:
            if kind != "decisao":
                return {"criada": False, "path": None, "motivo": "Só decisões podem substituir decisões."}
            previous = con.execute("SELECT * FROM notes WHERE path=?", (supersedes,)).fetchone()
            if previous is None:
                candidates = con.execute("SELECT * FROM notes WHERE lower(title)=lower(?)", (supersedes,)).fetchall()
                if len(candidates) != 1:
                    return {"criada": False, "path": None,
                            "motivo": "Informe o caminho exato da decisão a substituir; título ausente ou ambíguo."}
                previous = candidates[0]
            old_fm = json.loads(previous["frontmatter"] or "{}")
            if old_fm.get("tipo") != "decisao" or old_fm.get("status", "ativa") != "ativa":
                return {"criada": False, "path": None, "motivo": "A substituída precisa ser uma decisão ativa."}
            force = True  # é de propósito parecida com a antiga
            fm["substitui"] = f"[[{previous['title']}]]"
            fm["substitui_arquivo"] = previous["path"]
        warning = None
        dup = None if force else _similar_note(vault, con, kind, dedupe_text)
        if dup and dup[1] >= DUPLICATE_THRESHOLD and has_conflict(dup[2], dedupe_text):
            row, score, _ = dup
            if mode == "automatico":
                return {"criada":False,"path":None,"revisao_necessaria":True,
                        "candidata":row['path'],"motivo":"Valores diferentes no mesmo assunto; confirme substitui explicitamente."}
            else:
                warning = (f"mesmo assunto de [[{row['title']}]] com valores diferentes: parece uma MUDANÇA. "
                         f"Se for, registre de novo com substitui=\"{row['title']}\".")
                dup = None
        if dup:
            row, score, _ = dup
            if score >= SIMILAR_THRESHOLD and score < DUPLICATE_THRESHOLD:
                warning = (f"parecida com [[{row['title']}]] ({score:.2f}). Se esta decisão muda aquela, "
                         f"registre de novo com substitui=\"{row['title']}\".")
            if score >= DUPLICATE_THRESHOLD:
                # mesmo assunto: em vez de criar outra nota, acrescenta à existente só o que é novo
                con.close()
                up = update_note(vault, row["path"], body, origin=fm.get("origem"), session_id=fm.get("sessao"))
                base = {"criada": False, "path": row["path"], "similaridade": round(score, 3)}
                if up["atualizada"]:
                    return {**base, "atualizada": True, "acrescentado": up["acrescentado"]}
                return {**base, "atualizada": False, "motivo": f"já registrado em [[{row['title']}]] e sem nada novo."}
        if fm.get("projeto"):
            fm["projeto"] = _project_link(con, fm["projeto"])
    finally:
        con.close()

    today = dt.date.today().isoformat()
    folder = notes_dir(vault) / MEMORY_FOLDERS[kind]
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{today} {_slug(title)}"
    path = folder / f"{base}.md"
    n = 2
    while path.exists():
        path = folder / f"{base} {n}.md"
        n += 1
    front = {"title": title, "tipo": kind, "data": today, **{k: v for k, v in fm.items() if v}}
    text = "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in front.items()) + f"\n---\n\n# {title}\n\n{body.strip()}\n"
    editor.save(vault, path.relative_to(vault).as_posix(), text, create=True)
    if previous is not None:  # invalida sem apagar: a antiga continua no histórico
        _set_frontmatter(vault, vault / previous["path"], {"status": "substituída", "substituida_por": f"[[{title}]]",
                                                  "substituida_por_arquivo": path.relative_to(vault).as_posix(),
                                                  "substituida_em": today})
    index_vault(vault, quiet=True)
    out = {"criada": True, "path": str(path.relative_to(vault)), "relevancia": av.score}
    if av.reasons:
        out["pontos_fracos"] = av.reasons
    if warning:
        out["aviso"] = warning
    if previous is not None:
        out["substituiu"] = previous["path"]
    return out


def _section(name: str, content) -> str:
    if not content:
        return ""
    if isinstance(content, (list, tuple)):
        content = "\n".join(f"- {c}" for c in content if c)
    return f"## {name}\n\n{str(content).strip()}\n\n"


def record_decision(vault: Path, title: str, decision: str, context: str = "", reason: str = "",
                      alternatives: list[str] | None = None, consequences: str = "", project: str | None = None,
                      tags: list[str] | None = None, origin: str | None = None, force: bool = False,
                      supersedes: str | None = None, session_id: str | None = None, mode: str = "manual",
                      llm: dict | None = None) -> dict:
    body = (
        _section("Decisão", decision) + _section("Contexto", context) + _section("Por quê", reason)
        + _section("Alternativas consideradas", alternatives) + _section("Consequências", consequences)
    )
    fm = {"status": "ativa", "projeto": project, "origem": origin, "sessao": session_id,
          "tags": sorted({"decisao", *[t.lstrip("#").lower() for t in (tags or [])]})}
    return _write_memory(vault, "decisao", title, fm, body, core_subject(title, decision), force, supersedes,
                     mode=mode, reason=reason, llm=llm)


@editor.serialized
def review_decision(vault: Path, previous: str, action: str, reason: str,
                    replacement: str | None = None) -> dict:
    """Retira uma decisão existente do contexto vigente, preservando arquivo e histórico.

    Exige caminhos exatos e uma justificativa revisada; similaridade nunca revoga decisões sozinha.
    """
    if action not in ("substituir", "revogar"):
        raise editor.EditError("Ação deve ser substituir ou revogar")
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise editor.EditError("Informe o motivo da revisão (ao menos 10 caracteres)")
    if (action == "substituir") != bool(replacement):
        raise editor.EditError("Informe substituta somente para a ação substituir")
    index_vault(vault, quiet=True)
    con = connect(vault)
    try:
        old = con.execute("SELECT path, title, frontmatter FROM notes WHERE path=?", (previous,)).fetchone()
        if old is None or json.loads(old["frontmatter"] or "{}").get("tipo") != "decisao":
            raise editor.EditError("Decisão antiga não encontrada pelo caminho exato")
        old_fm = json.loads(old["frontmatter"] or "{}")
        if old_fm.get("status", "ativa") != "ativa":
            raise editor.EditError("A decisão antiga já não está ativa")
        new = None
        if replacement:
            new = con.execute("SELECT path, title, frontmatter FROM notes WHERE path=?", (replacement,)).fetchone()
            if (new is None or new["path"] == old["path"]
                    or json.loads(new["frontmatter"] or "{}").get("tipo") != "decisao"
                    or json.loads(new["frontmatter"] or "{}").get("status", "ativa") != "ativa"):
                raise editor.EditError("Substituta deve ser outra decisão ativa, pelo caminho exato")
        path, title = old["path"], old["title"]
        successor = dict(new) if new else None
    finally:
        con.close()
    updates = {"status": "substituída" if successor else "revogada",
               "motivo_revisao": reason.strip(), "revisada_em": dt.date.today().isoformat()}
    if successor:
        updates.update(substituida_por=f"[[{successor['title']}]]",
                       substituida_por_arquivo=successor["path"])
    snapshot = editor.read(vault, path)
    editor.save(vault, path, _frontmatter(snapshot["content"], updates), snapshot["revision"])
    index_vault(vault, quiet=True)
    return {"path": path, "titulo": title, "status": updates["status"],
            "substituta": successor["path"] if successor else None}


def record_learning(vault: Path, title: str, content: str, project: str | None = None,
                          tags: list[str] | None = None, origin: str | None = None, force: bool = False,
                          session_id: str | None = None, mode: str = "manual", llm: dict | None = None) -> dict:
    fm = {"projeto": project, "origem": origin, "sessao": session_id,
          "tags": sorted({"aprendizado", *[t.lstrip("#").lower() for t in (tags or [])]})}
    return _write_memory(vault, "aprendizado", title, fm, content, core_subject(title, content), force,
                     mode=mode, llm=llm)
