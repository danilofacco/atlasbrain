"""Escrita estruturada no cérebro: decisões e aprendizados viram markdown com frontmatter,
ligados ao projeto por [[wikilink]] e sem duplicar o que já foi registrado."""

import datetime as dt
import json
import re
from pathlib import Path

import numpy as np

from . import embed, editor
from .config import notes_dir
from .relevancia import avaliar
from .db import connect, from_blob
from .indexer import index_vault

PASTAS = {"decisao": "Decisões", "aprendizado": "Aprendizados"}
DUPLICADO_MIN = 0.85
PARECIDA_MIN = 0.72  # abaixo de duplicata, mas talvez uma mudança da mesma decisão


def _slug(titulo: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "", titulo).strip()[:90] or "Sem título"


def _yaml(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _projeto_link(con, projeto: str | None) -> str | None:
    if not projeto:
        return None
    nome = projeto.strip().strip("[]")
    row = con.execute(
        "SELECT title FROM notes WHERE lower(title)=? OR lower(path) LIKE ? ORDER BY length(path) LIMIT 1",
        (nome.lower(), f"%/{nome.lower()}.md"),
    ).fetchone()
    return f"[[{row['title'] if row else nome}]]"


def _primeira_frase(texto: str) -> str:
    texto = " ".join(texto.split())
    m = re.search(r"^(.{15,}?[.!?])(\s|$)", texto)
    return (m.group(1) if m else texto)[:300]


def nucleo(titulo: str, texto: str) -> str:
    """O assunto de uma nota: título + primeira frase. Detalhe novo não muda o núcleo, então uma nota
    com mais informação continua sendo reconhecida como o mesmo assunto (e vira atualização)."""
    return f"{titulo}. {_primeira_frase(texto)}"


def _essencia(vault: Path, row) -> str:
    """Núcleo de uma nota já gravada: título + primeira frase da seção Decisão (ou do corpo)."""
    try:
        text = (vault / row["path"]).read_text(encoding="utf-8")
    except OSError:
        return row["title"]
    m = re.search(r"## Decisão\s+(.+?)(?:\n## |\Z)", text, re.S)
    body = m.group(1) if m else re.sub(r"^---.*?---\s*(# .*?\n)?", "", text, flags=re.S)
    return nucleo(row["title"], body)


_NUMEROS = re.compile(r"\d+(?:[.,]\d+)?")


def conflito(nucleo_antigo: str, nucleo_novo: str) -> bool:
    """Mesmo assunto com valores diferentes (R$ 97 → R$ 147, 3 → 5 réplicas): é uma MUDANÇA, não um detalhe."""
    a, b = set(_NUMEROS.findall(nucleo_antigo)), set(_NUMEROS.findall(nucleo_novo))
    return bool(a and b and a != b)


def _parecida(vault: Path, con, tipo: str, texto: str):
    """Maior similaridade entre o texto novo e as notas ativas do mesmo tipo (título + miolo contra
    título + miolo). Medido: duplicatas 0,88–0,91; decisões diferentes 0,65–0,76."""
    rows = [
        r for r in con.execute("SELECT path, title, frontmatter FROM notes")
        if (fm := json.loads(r["frontmatter"] or "{}")).get("tipo") == tipo and fm.get("status", "ativa") == "ativa"
    ]
    if not rows:
        return None
    nucleos = [_essencia(vault, r) for r in rows]
    normalized = lambda value: ' '.join(value.casefold().split())
    for row, core in zip(rows, nucleos):
        if normalized(core)==normalized(texto):
            return row, 1.0, core
    if not embed.enabled():
        return None
    V = embed.embed([texto] + nucleos)
    scores = V[1:] @ V[0]
    i = int(np.argmax(scores))
    return rows[i], float(scores[i]), nucleos[i]


_FRASE = re.compile(r"(?<=[.!?])\s+|\n+(?:[-*]\s+)?")
_CONCRETO = re.compile(r"\b\d+(?:[.,]\d+)?\b|[\w.-]+/[\w./-]+|`[^`]+`|\b[a-z]+[A-Z]\w*\b|\b\w+_\w+\b|R\$\s?\d+")


def _frases(texto: str) -> list[str]:
    texto = re.sub(r"^---.*?---\s*", "", texto, flags=re.S)
    texto = re.sub(r"^#+ .*$", "", texto, flags=re.M)
    return [f.strip(" -*") for f in _FRASE.split(texto) if len(f.strip(" -*")) >= 15]


def novidades(texto_antigo: str, texto_novo: str, limiar: float = 0.8) -> list[str]:
    """Frases do texto novo que a nota antiga ainda não tem. Uma frase é nova se traz algo concreto
    (número, arquivo, identificador) ausente da nota, ou se o sentido dela não está em nenhuma frase antiga."""
    antigas, novas = _frases(texto_antigo), _frases(texto_novo)
    if not novas:
        return []
    conhecidos = {m.lower() for m in _CONCRETO.findall(texto_antigo)}
    candidatas = []
    for f in novas:
        concretos = {m.lower() for m in _CONCRETO.findall(f)}
        if concretos - conhecidos:
            candidatas.append((f, True))  # número/arquivo/nome que a nota não tinha: é novidade com certeza
        else:
            candidatas.append((f, False))
    duvidosas = [f for f, certo in candidatas if not certo]
    novas_por_sentido = set()
    if duvidosas and antigas and embed.enabled():
        V = embed.embed(duvidosas + antigas)
        sim = V[: len(duvidosas)] @ V[len(duvidosas):].T
        novas_por_sentido = {duvidosas[i] for i in range(len(duvidosas)) if sim[i].max() < limiar}
    elif duvidosas and not antigas:
        novas_por_sentido = set(duvidosas)
    elif duvidosas:  # sem embeddings: compara palavras
        ant = set(re.findall(r"\w{4,}", texto_antigo.lower()))
        for f in duvidosas:
            pal = set(re.findall(r"\w{4,}", f.lower()))
            if pal and len(pal - ant) / len(pal) > 0.5:
                novas_por_sentido.add(f)
    return [f for f, certo in candidatas if certo or f in novas_por_sentido]


@editor.serialized
def atualizar_nota(vault: Path, alvo: str, texto: str, origem: str | None = None, sessao: str | None = None,
                   so_novidades: bool = True, revision: str | None = None) -> dict:
    """Acrescenta a uma nota existente só o que é novo, numa seção datada "## Atualizações".
    Nunca apaga nem reescreve o que já estava lá: o histórico fica legível."""
    from .graph import find_note
    con = connect(vault)
    try:
        row = find_note(con, alvo)
    finally:
        con.close()
    if row is None:
        return {"atualizada": False, "path": None, "motivo": f"Nota não encontrada: '{alvo}'."}
    path = vault / row["path"]
    if row["kind"] != "nota" or not path.exists():
        return {"atualizada": False, "path": row["path"], "motivo": "Só dá para atualizar notas markdown."}
    snapshot = editor.read(vault, row['path'])
    atual = snapshot['content']
    if revision and revision != snapshot['revision']:
        raise editor.EditError('A nota mudou. Leia novamente antes de atualizar.', 409)
    novas = novidades(atual, texto) if so_novidades else _frases(texto) or [texto.strip()]
    if not novas:
        return {"atualizada": False, "path": row["path"], "motivo": "Nada novo: a nota já diz isso."}
    hoje = dt.date.today().isoformat()
    quem = f" ({origem})" if origem else ""
    bloco = f"- **{hoje}**{quem}: " + " ".join(n.rstrip(".") + "." for n in novas)
    if "\n## Atualizações" in atual:
        corpo = atual.rstrip() + "\n" + bloco + "\n"
    else:
        corpo = atual.rstrip() + "\n\n## Atualizações\n\n" + bloco + "\n"
    from .parse import split_frontmatter
    fm = split_frontmatter(corpo)[0]
    extra = {"atualizado": hoje, "atualizacoes": int(fm.get("atualizacoes", 0) or 0) + 1}
    if sessao:
        extra["sessoes"] = sorted({*(fm.get("sessoes") or []), *( [fm["sessao"]] if fm.get("sessao") else []), sessao})
    editor.save(vault, row['path'], _frontmatter(corpo, extra), snapshot['revision'])
    index_vault(vault, quiet=True)
    return {"atualizada": True, "path": row["path"], "acrescentado": novas}


def _frontmatter(text: str, updates: dict) -> str:
    from .parse import split_frontmatter
    fm, body = split_frontmatter(text)
    fm.update(updates)
    return "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in fm.items()) + "\n---\n" + body


def _set_frontmatter(vault: Path, path: Path, updates: dict) -> None:
    snapshot = editor.read(vault, path.relative_to(vault).as_posix())
    editor.save(vault, snapshot['path'], _frontmatter(snapshot['content'], updates), snapshot['revision'])


@editor.serialized
def _escrever(vault: Path, tipo: str, titulo: str, fm: dict, corpo: str, dedupe_texto: str, forcar: bool,
              substitui: str | None = None, modo: str = "manual", motivo: str = "", llm: dict | None = None) -> dict:
    """modo="manual": o agente pediu (portão frouxo, só barra o claramente inútil).
    modo="automatico": a captura decidiu sozinha (portão exigente)."""
    conteudo = re.sub(r"^## .*$", "", corpo, flags=re.M)  # avalia o que foi dito, não os cabeçalhos do modelo
    av = avaliar(tipo, titulo, conteudo, motivo, modo=modo, llm=llm)
    if not av.aceito and not forcar:
        return {"criada": False, "path": None, "relevancia": av.nota,
                "motivo": f"Não parece valer uma nota ({av.resumo()}). Se vale, complete ou use forcar=True."}
    fm["relevancia"] = av.nota
    index_vault(vault, quiet=True)
    con = connect(vault)
    antiga = None
    try:
        if substitui and modo == 'automatico':
            return {'criada':False,'path':None,'revisao_necessaria':True,'candidata':substitui,
                    'motivo':'A captura propôs uma substituição; confirme pelo registro manual.'}
        if substitui:
            if tipo != "decisao":
                return {"criada": False, "path": None, "motivo": "Só decisões podem substituir decisões."}
            antiga = con.execute("SELECT * FROM notes WHERE path=?", (substitui,)).fetchone()
            if antiga is None:
                candidates = con.execute("SELECT * FROM notes WHERE lower(title)=lower(?)", (substitui,)).fetchall()
                if len(candidates) != 1:
                    return {"criada": False, "path": None,
                            "motivo": "Informe o caminho exato da decisão a substituir; título ausente ou ambíguo."}
                antiga = candidates[0]
            old_fm = json.loads(antiga["frontmatter"] or "{}")
            if old_fm.get("tipo") != "decisao" or old_fm.get("status", "ativa") != "ativa":
                return {"criada": False, "path": None, "motivo": "A substituída precisa ser uma decisão ativa."}
            forcar = True  # é de propósito parecida com a antiga
            fm["substitui"] = f"[[{antiga['title']}]]"
            fm["substitui_arquivo"] = antiga["path"]
        aviso = None
        dup = None if forcar else _parecida(vault, con, tipo, dedupe_texto)
        if dup and dup[1] >= DUPLICADO_MIN and conflito(dup[2], dedupe_texto):
            row, score, _ = dup
            if modo == "automatico":
                return {"criada":False,"path":None,"revisao_necessaria":True,
                        "candidata":row['path'],"motivo":"Valores diferentes no mesmo assunto; confirme substitui explicitamente."}
            else:
                aviso = (f"mesmo assunto de [[{row['title']}]] com valores diferentes: parece uma MUDANÇA. "
                         f"Se for, registre de novo com substitui=\"{row['title']}\".")
                dup = None
        if dup:
            row, score, _ = dup
            if score >= PARECIDA_MIN and score < DUPLICADO_MIN:
                aviso = (f"parecida com [[{row['title']}]] ({score:.2f}). Se esta decisão muda aquela, "
                         f"registre de novo com substitui=\"{row['title']}\".")
            if score >= DUPLICADO_MIN:
                # mesmo assunto: em vez de criar outra nota, acrescenta à existente só o que é novo
                con.close()
                up = atualizar_nota(vault, row["path"], corpo, origem=fm.get("origem"), sessao=fm.get("sessao"))
                base = {"criada": False, "path": row["path"], "similaridade": round(score, 3)}
                if up["atualizada"]:
                    return {**base, "atualizada": True, "acrescentado": up["acrescentado"]}
                return {**base, "atualizada": False, "motivo": f"já registrado em [[{row['title']}]] e sem nada novo."}
        if fm.get("projeto"):
            fm["projeto"] = _projeto_link(con, fm["projeto"])
    finally:
        con.close()

    hoje = dt.date.today().isoformat()
    folder = notes_dir(vault) / PASTAS[tipo]
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{hoje} {_slug(titulo)}"
    path = folder / f"{base}.md"
    n = 2
    while path.exists():
        path = folder / f"{base} {n}.md"
        n += 1
    front = {"title": titulo, "tipo": tipo, "data": hoje, **{k: v for k, v in fm.items() if v}}
    text = "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in front.items()) + f"\n---\n\n# {titulo}\n\n{corpo.strip()}\n"
    editor.save(vault, path.relative_to(vault).as_posix(), text, create=True)
    if antiga is not None:  # invalida sem apagar: a antiga continua no histórico
        _set_frontmatter(vault, vault / antiga["path"], {"status": "substituída", "substituida_por": f"[[{titulo}]]",
                                                  "substituida_por_arquivo": path.relative_to(vault).as_posix(),
                                                  "substituida_em": hoje})
    index_vault(vault, quiet=True)
    out = {"criada": True, "path": str(path.relative_to(vault)), "relevancia": av.nota}
    if av.motivos:
        out["pontos_fracos"] = av.motivos
    if aviso:
        out["aviso"] = aviso
    if antiga is not None:
        out["substituiu"] = antiga["path"]
    return out


def _secao(nome: str, conteudo) -> str:
    if not conteudo:
        return ""
    if isinstance(conteudo, (list, tuple)):
        conteudo = "\n".join(f"- {c}" for c in conteudo if c)
    return f"## {nome}\n\n{str(conteudo).strip()}\n\n"


def registrar_decisao(vault: Path, titulo: str, decisao: str, contexto: str = "", motivo: str = "",
                      alternativas: list[str] | None = None, consequencias: str = "", projeto: str | None = None,
                      tags: list[str] | None = None, origem: str | None = None, forcar: bool = False,
                      substitui: str | None = None, sessao: str | None = None, modo: str = "manual",
                      llm: dict | None = None) -> dict:
    corpo = (
        _secao("Decisão", decisao) + _secao("Contexto", contexto) + _secao("Por quê", motivo)
        + _secao("Alternativas consideradas", alternativas) + _secao("Consequências", consequencias)
    )
    fm = {"status": "ativa", "projeto": projeto, "origem": origem, "sessao": sessao,
          "tags": sorted({"decisao", *[t.lstrip("#").lower() for t in (tags or [])]})}
    return _escrever(vault, "decisao", titulo, fm, corpo, nucleo(titulo, decisao), forcar, substitui,
                     modo=modo, motivo=motivo, llm=llm)


@editor.serialized
def revisar_decisao(vault: Path, antiga: str, acao: str, motivo: str,
                    substituta: str | None = None) -> dict:
    """Retira uma decisão existente do contexto vigente, preservando arquivo e histórico.

    Exige caminhos exatos e uma justificativa revisada; similaridade nunca revoga decisões sozinha.
    """
    if acao not in ("substituir", "revogar"):
        raise editor.EditError("Ação deve ser substituir ou revogar")
    if not isinstance(motivo, str) or len(motivo.strip()) < 10:
        raise editor.EditError("Informe o motivo da revisão (ao menos 10 caracteres)")
    if (acao == "substituir") != bool(substituta):
        raise editor.EditError("Informe substituta somente para a ação substituir")
    index_vault(vault, quiet=True)
    con = connect(vault)
    try:
        old = con.execute("SELECT path, title, frontmatter FROM notes WHERE path=?", (antiga,)).fetchone()
        if old is None or json.loads(old["frontmatter"] or "{}").get("tipo") != "decisao":
            raise editor.EditError("Decisão antiga não encontrada pelo caminho exato")
        old_fm = json.loads(old["frontmatter"] or "{}")
        if old_fm.get("status", "ativa") != "ativa":
            raise editor.EditError("A decisão antiga já não está ativa")
        new = None
        if substituta:
            new = con.execute("SELECT path, title, frontmatter FROM notes WHERE path=?", (substituta,)).fetchone()
            if (new is None or new["path"] == old["path"]
                    or json.loads(new["frontmatter"] or "{}").get("tipo") != "decisao"
                    or json.loads(new["frontmatter"] or "{}").get("status", "ativa") != "ativa"):
                raise editor.EditError("Substituta deve ser outra decisão ativa, pelo caminho exato")
        path, title = old["path"], old["title"]
        successor = dict(new) if new else None
    finally:
        con.close()
    updates = {"status": "substituída" if successor else "revogada",
               "motivo_revisao": motivo.strip(), "revisada_em": dt.date.today().isoformat()}
    if successor:
        updates.update(substituida_por=f"[[{successor['title']}]]",
                       substituida_por_arquivo=successor["path"])
    snapshot = editor.read(vault, path)
    editor.save(vault, path, _frontmatter(snapshot["content"], updates), snapshot["revision"])
    index_vault(vault, quiet=True)
    return {"path": path, "titulo": title, "status": updates["status"],
            "substituta": successor["path"] if successor else None}


def registrar_aprendizado(vault: Path, titulo: str, conteudo: str, projeto: str | None = None,
                          tags: list[str] | None = None, origem: str | None = None, forcar: bool = False,
                          sessao: str | None = None, modo: str = "manual", llm: dict | None = None) -> dict:
    fm = {"projeto": projeto, "origem": origem, "sessao": sessao,
          "tags": sorted({"aprendizado", *[t.lstrip("#").lower() for t in (tags or [])]})}
    return _escrever(vault, "aprendizado", titulo, fm, conteudo, nucleo(titulo, conteudo), forcar,
                     modo=modo, llm=llm)
