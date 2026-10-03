import json
import io
import subprocess
import sys
from types import SimpleNamespace

import pytest

from atlasbrain import capture
from atlasbrain.parse import split_frontmatter
from atlasbrain.memory import record_learning, record_decision, review_decision


def fm(vault, rel):
    return split_frontmatter((vault / rel).read_text())[0]


def test_decisao_vai_para_atlasbrain_com_frontmatter(project):
    r = record_decision(project, "Usar SQLite no MVP", "Banco local SQLite.", reason="Simples",
                          alternatives=["Postgres"], project="projeto", session_id="abc")
    assert r["criada"] and r["path"].startswith(".atlasbrain/Decisions/")
    meta = fm(project, r["path"])
    assert meta["tipo"] == "decisao" and meta["status"] == "ativa" and meta["sessao"] == "abc"
    text = (project / r["path"]).read_text()
    assert "## Por quê" in text and "- Postgres" in text


def test_nunca_sobrescreve(project):
    a = record_decision(project, "Mesmo título", "A", force=True)
    b = record_decision(project, "Mesmo título", "B totalmente diferente", force=True)
    assert a["path"] != b["path"]
    assert not record_decision(project, 'Outra decisão', 'Mudou a regra anterior.', supersedes='Mesmo título')['criada']


def test_supersessao(project):
    assert record_decision(project, "Preço Pro 97", "Plano Pro a R$ 97.")["criada"]  # curta mas concreta
    r = record_decision(project, "Preço Pro 147", "Plano Pro a R$ 147.", supersedes="Preço Pro 97")
    assert r["substituiu"].endswith("Preço Pro 97.md")
    previous = fm(project, r["substituiu"])
    assert previous["status"] == "substituída" and previous["substituida_por"] == "[[Preço Pro 147]]"
    assert fm(project, r["path"])["substitui"] == "[[Preço Pro 97]]"
    assert not record_decision(project, "X", "Y", supersedes="não existe")["criada"]


def test_revisar_decisoes_ja_existentes_preserva_historico(project):
    old = record_decision(project, "Preço antigo", "Plano Pro custa R$ 97 por mês.", force=True)
    new = record_decision(project, "Preço novo", "Plano Pro custa R$ 147 por mês.", force=True)
    result = review_decision(project, old['path'], 'substituir',
                             'Novo preço aprovado para o mesmo plano.', new['path'])
    assert result['status'] == 'substituída' and result['substituta'] == new['path']
    assert (project / old['path']).exists()
    assert fm(project, old['path'])['substituida_por_arquivo'] == new['path']
    assert fm(project, new['path'])['status'] == 'ativa'
    with pytest.raises(ValueError, match='já não está ativa'):
        review_decision(project, old['path'], 'revogar', 'Foi cancelada em revisão posterior.')

    other = record_decision(project, 'Usar ferramenta X', 'Adotar a ferramenta X no projeto.', force=True)
    revoked = review_decision(project, other['path'], 'revogar', 'A ferramenta saiu do projeto.')
    assert revoked['status'] == 'revogada' and revoked['substituta'] is None
    assert fm(project, other['path'])['status'] == 'revogada'


@pytest.mark.embeddings
def test_dedupe_calibrado(project, com_embeddings):
    record_decision(project, "Embeddings com e5-large multilíngue", "Trocar para intfloat/multilingual-e5-large.")
    dup = record_decision(project, "Modelo de embeddings e5-large", "Trocar o modelo de embeddings para multilingual-e5-large.")
    assert not dup["criada"] and dup["similaridade"] >= 0.85
    record_decision(project, "Preço do plano Pro é R$ 97", "Definimos o plano Pro a R$ 97 por mês.")
    mud = record_decision(project, "Preço do plano Pro é R$ 147", "Definimos o plano Pro a R$ 147 por mês.")
    assert mud["criada"] and "substitui=" in mud["aviso"]  # mudança, não duplicata
    ok = record_decision(project, "Deploy na Vercel", "Hospedar o frontend na Vercel.")
    assert ok["criada"] and "aviso" not in ok


def test_aprendizado(project):
    r = record_learning(project, "SDK MCP v2 renomeou FastMCP",
                              "Na versão 2 do SDK, FastMCP virou MCPServer em mcp.server.mcpserver; código da v1 quebra.")
    assert r["path"].startswith(".atlasbrain/Learnings/") and fm(project, r["path"])["tipo"] == "aprendizado"


# ------------------------------------------------------------------ captura de transcripts

def test_le_transcript_claude_e_codex(tmp_path):
    claude = tmp_path / "c.jsonl"
    claude.write_text("\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"role": "user", "content": "Vamos usar SQLite?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "segredo"}, {"type": "text", "text": "Sim, SQLite."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "saida do ls"}]}},
        {"type": "user", "message": {"role": "user", "content": "<system-reminder>ignorar</system-reminder>"}},
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "subagente"}},
    ]))
    t = capture.read_transcript(claude)
    assert "[USUÁRIO] Vamos usar SQLite?" in t and "[ASSISTENTE] Sim, SQLite." in t
    assert not any(x in t for x in ("segredo", "saida do ls", "ignorar", "subagente"))

    codex = tmp_path / "x.jsonl"
    codex.write_text("\n".join(json.dumps(x) for x in [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Oi Codex"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Feito."}]}},
    ]))
    t = capture.read_transcript(codex)
    assert "[USUÁRIO] Oi Codex" in t and "[ASSISTENTE] Feito." in t and "shell" not in t


def test_worker_usa_extrator_e_nao_repete(project, tmp_path, monkeypatch):
    tr = tmp_path / "t.jsonl"
    tr.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Decidimos usar Redis para fila. " * 400}}))
    calls = []

    def falso(prompt):
        calls.append(prompt)
        return {"decisoes": [
                    {"titulo": "Redis para fila", "decisao": "Vamos usar Redis como fila de jobs no lugar do cron.",
                     "motivo": "O cron perdia jobs quando o servidor reiniciava.", "importancia": 4, "durabilidade": 5},
                    {"titulo": "Rodar os testes", "decisao": "Agora vou rodar os testes e ver se passa.",
                     "importancia": 1, "durabilidade": 1}],
                "aprendizados": []}

    monkeypatch.setattr(capture, "_extract", falso)
    capture.worker(project, tr, "s1", str(project), "Stop")
    assert list((project / ".atlasbrain" / "Decisions").glob("*Redis para fila.md"))
    assert "Data da conversa:" in calls[0]
    capture.worker(project, tr, "s1", str(project), "Stop")  # nada novo desde a última captura
    assert len(calls) == 1
    log = (project / ".atlasbrain" / "capture.log").read_text()
    assert "Redis para fila" in log
    assert "✗ recusada “Rodar os testes”" in log  # passo de execução não vira nota
    assert not list((project / ".atlasbrain" / "Decisions").glob("*Rodar os testes*"))


def test_hook_nao_recursivo_e_nao_trava(project, monkeypatch):
    monkeypatch.setenv("ATLASBRAIN_CAPTURANDO", "1")
    r = subprocess.run([sys.executable, "-m", "atlasbrain.cli", "capturar"], input="{}", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout == ""


@pytest.mark.parametrize('setting,opt_in,launch', [
    (None, False, False), (None, True, True), ('1', False, True),
    ('0', True, False), ('invalid', False, False),
])
def test_automatic_capture_requires_explicit_opt_in(project, tmp_path, monkeypatch, setting, opt_in, launch):
    monkeypatch.delenv('ATLASBRAIN_CAPTURANDO', raising=False)
    monkeypatch.delenv('ATLASBRAIN_CAPTURA', raising=False)
    if setting is not None:
        monkeypatch.setenv('ATLASBRAIN_CAPTURA', setting)
    transcript = tmp_path / 'conversation.jsonl'
    transcript.write_text('{}')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({
        'cwd': str(project), 'transcript_path': str(transcript), 'session_id': 's1',
    })))
    launches = []
    monkeypatch.setattr('atlasbrain.config.resolve_vault', lambda *args, **kwargs: project)
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kwargs: launches.append((args, kwargs)))
    capture.hook(opt_in=opt_in)
    assert bool(launches) is launch
    if launch:
        args, options = launches[0]
        assert '--auto' in args and '--worker' in args
        assert options['env']['ATLASBRAIN_CAPTURANDO'] == '1'
    else:
        assert sys.stdin.tell() == 0
        assert not (project / '.atlasbrain').exists()


def test_old_queued_workers_stay_disabled_and_manual_capture_still_works(project, monkeypatch):
    from atlasbrain.cli import cmd_capture
    monkeypatch.delenv('ATLASBRAIN_CAPTURA', raising=False)
    calls = []
    monkeypatch.setattr(capture, 'worker', lambda *args: calls.append(args))
    args = SimpleNamespace(worker=True, auto=False, transcript='conversation.jsonl',
                           vault=str(project), event='Stop', session_id='s1', cwd=str(project))
    cmd_capture(args)
    assert calls == []
    args.worker = False
    cmd_capture(args)
    assert len(calls) == 1 and calls[0][-1] == 'SessionEnd'


def test_briefing_prioriza_projeto_atual(project):
    record_decision(project, "Decisão de outro", "x", project="outro", force=True)
    record_decision(project, "Decisão daqui", "y", project=project.name, force=True)
    b = capture.briefing(project, str(project))
    assert b.index("Decisão daqui") < b.index("Decisão de outro")
    assert "record_decision" in b


def test_briefing_supplies_content_paths_and_keeps_historical_decisions_out(project):
    old = record_decision(project, 'Intervalo antigo', 'Verificar arquivos a cada três segundos.', force=True)
    current = record_decision(project, 'Intervalo atual', 'Verificar arquivos uma vez por minuto.',
                              supersedes=old['path'], force=True)
    learning = record_learning(project, 'Console dos processos filhos',
                               'Redirecionar stdout não impede a abertura de consoles no Windows.', force=True)
    b = capture.briefing(project, str(project))
    assert 'Verificar arquivos uma vez por minuto.' in b
    assert current['path'] in b and learning['path'] in b
    assert 'Redirecionar stdout não impede' in b
    assert 'Intervalo antigo' not in b and old['path'] not in b
    assert 'not full-note reads' in b and 'read or read_many' in b
    assert 'actually consulted' in b


def test_briefing_bounds_output_without_cutting_note_paths(project):
    paths = []
    for i in range(12):
        result = record_decision(project, f'Decisão {i} com título extenso ' + 'a' * 60,
                                 'Usar a estratégia ' + str(i) + '. ' + 'Contexto extenso. ' * 30, force=True)
        paths.append(result['path'])
    b = capture.briefing(project, str(project), limit=20)
    assert len(b) <= 4000
    assert 'additional entries omitted' in b
    for line in b.splitlines():
        if line.startswith('  Path: '):
            assert line.removeprefix('  Path: `').removesuffix('`') in paths
            assert line.endswith('.md`')


# ------------------------------------------------------------------ portão de relevância

@pytest.mark.parametrize("kind,title,text,reason,passa", [
    ("decisao", "Usar SQLite no MVP", "Vamos usar SQLite local em vez de Postgres na primeira versão.", "Menos infra.", True),
    ("decisao", "Preço do plano Pro", "Definimos o plano Pro a R$ 147 por mês a partir de outubro.", "", True),
    ("aprendizado", "SDK MCP v2", "Na versão 2 do SDK do MCP, FastMCP virou MCPServer em mcp.server.mcpserver; código da v1 quebra.", "", True),
    ("decisao", "Rodar os testes", "Agora vou rodar os testes e ver se passa.", "", False),
    ("decisao", "Talvez trocar o banco", "Talvez a gente possa pensar em usar outro banco no futuro.", "", False),
    ("aprendizado", "Corrigi o typo", "Corrigi o typo no README e fiz o commit.", "", False),
    ("decisao", "Melhorar o código", "Precisamos melhorar a qualidade do código de modo geral.", "", False),
    ("aprendizado", "Python é bom", "Python é uma linguagem de programação muito usada.", "", False),
])
def test_portao_de_relevancia(kind, title, text, reason, passa):
    from atlasbrain.relevance import evaluate
    a = evaluate(kind, title, text, reason, mode="automatico")
    assert a.accepted is passa, a.summary()
    if not passa:
        assert a.reasons  # toda recusa diz o porquê


def test_manual_barra_so_o_inutil(project):
    r = record_decision(project, "ok", "curto")
    assert not r["criada"] and "relevância" in r["motivo"]
    r = record_decision(project, "Rodar os testes", "Agora vou rodar os testes e ver se passa.")
    assert not r["criada"]
    r = record_decision(project, "Rodar os testes", "Agora vou rodar os testes e ver se passa.", force=True)
    assert r["criada"]  # o agente pode insistir


# ------------------------------------------------------------------ atualizar em vez de duplicar

def test_novidades_por_frase():
    from atlasbrain.memory import new_information
    antigo = "# Fila\n\n## Decisão\n\nUsar Redis como fila de jobs. O cron perdia jobs no restart.\n"
    assert new_information(antigo, "Usar Redis como fila de jobs.") == []  # nada novo
    new_notes = new_information(antigo, "Usar Redis como fila de jobs. O timeout de cada job é 30 s.")
    assert new_notes == ["O timeout de cada job é 30 s."]  # número que a nota não tinha
    assert new_information(antigo, "Configuração em `lib/queue/redis.ts`.") == ["Configuração em `lib/queue/redis.ts`."]


def test_atualizar_nota_acrescenta_so_o_novo(project):
    from atlasbrain.memory import update_note
    r = record_decision(project, "Redis para fila", "Vamos usar Redis como fila de jobs.", reason="O cron perdia jobs.")
    up = update_note(project, "Redis para fila", "Vamos usar Redis como fila de jobs. Cada job tem timeout de 30 s.",
                        origin="teste", session_id="s9")
    assert up["atualizada"] and up["acrescentado"] == ["Cada job tem timeout de 30 s."]
    text = (project / r["path"]).read_text()
    assert text.count("## Atualizações") == 1 and "(teste): Cada job tem timeout de 30 s." in text
    assert "Vamos usar Redis" in text  # o original continua lá
    meta = fm(project, r["path"])
    assert meta["atualizacoes"] == 1 and "s9" in meta["sessoes"]
    assert not update_note(project, "Redis para fila", "Cada job tem timeout de 30 s.")["atualizada"]  # já tem
    assert update_note(project, "Redis para fila", "Workers rodam em 2 réplicas.")["atualizada"]
    assert (project / r["path"]).read_text().count("## Atualizações") == 1


@pytest.mark.embeddings
def test_mesmo_assunto_atualiza_em_vez_de_criar(project, com_embeddings):
    a = record_decision(project, "Embeddings com e5-large multilíngue", "Trocar para intfloat/multilingual-e5-large.",
                          reason="Mais precisão na busca.")
    b = record_decision(project, "Modelo de embeddings e5-large",
                          "Trocar o modelo de embeddings para multilingual-e5-large. Reindexação completa leva 3 min no olax.")
    assert not b["criada"] and b["atualizada"] and b["path"] == a["path"]
    assert any("3 min" in f for f in b["acrescentado"])
    assert len(list((project / ".atlasbrain" / "Decisions").glob("*.md"))) == 1


@pytest.mark.embeddings
def test_mudanca_de_valor_e_detectada(project, com_embeddings):
    record_decision(project, "Réplicas do worker", "Rodar 3 réplicas do worker de fila.", reason="Aguenta o pico de segunda.")
    manual = record_decision(project, "Réplicas do worker", "Rodar 5 réplicas do worker de fila.", reason="Pico cresceu.")
    assert manual["criada"] and "MUDANÇA" in manual["aviso"]
    auto = record_decision(project, "Réplicas do worker de fila", "Rodar 8 réplicas do worker de fila.",
                             reason="Black Friday dobrou o volume.", mode="automatico",
                             llm={"importancia": 4, "durabilidade": 4})
    assert not auto["criada"] and auto["revisao_necessaria"]
