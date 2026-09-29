import json
import subprocess
import sys

import pytest

from atlasbrain import captura
from atlasbrain.parse import split_frontmatter
from atlasbrain.registro import registrar_aprendizado, registrar_decisao, revisar_decisao


def fm(vault, rel):
    return split_frontmatter((vault / rel).read_text())[0]


def test_decisao_vai_para_atlasbrain_com_frontmatter(projeto):
    r = registrar_decisao(projeto, "Usar SQLite no MVP", "Banco local SQLite.", motivo="Simples",
                          alternativas=["Postgres"], projeto="projeto", sessao="abc")
    assert r["criada"] and r["path"].startswith(".atlasbrain/Decisões/")
    meta = fm(projeto, r["path"])
    assert meta["tipo"] == "decisao" and meta["status"] == "ativa" and meta["sessao"] == "abc"
    texto = (projeto / r["path"]).read_text()
    assert "## Por quê" in texto and "- Postgres" in texto


def test_nunca_sobrescreve(projeto):
    a = registrar_decisao(projeto, "Mesmo título", "A", forcar=True)
    b = registrar_decisao(projeto, "Mesmo título", "B totalmente diferente", forcar=True)
    assert a["path"] != b["path"]
    assert not registrar_decisao(projeto, 'Outra decisão', 'Mudou a regra anterior.', substitui='Mesmo título')['criada']


def test_supersessao(projeto):
    assert registrar_decisao(projeto, "Preço Pro 97", "Plano Pro a R$ 97.")["criada"]  # curta mas concreta
    r = registrar_decisao(projeto, "Preço Pro 147", "Plano Pro a R$ 147.", substitui="Preço Pro 97")
    assert r["substituiu"].endswith("Preço Pro 97.md")
    antiga = fm(projeto, r["substituiu"])
    assert antiga["status"] == "substituída" and antiga["substituida_por"] == "[[Preço Pro 147]]"
    assert fm(projeto, r["path"])["substitui"] == "[[Preço Pro 97]]"
    assert not registrar_decisao(projeto, "X", "Y", substitui="não existe")["criada"]


def test_revisar_decisoes_ja_existentes_preserva_historico(projeto):
    old = registrar_decisao(projeto, "Preço antigo", "Plano Pro custa R$ 97 por mês.", forcar=True)
    new = registrar_decisao(projeto, "Preço novo", "Plano Pro custa R$ 147 por mês.", forcar=True)
    result = revisar_decisao(projeto, old['path'], 'substituir',
                             'Novo preço aprovado para o mesmo plano.', new['path'])
    assert result['status'] == 'substituída' and result['substituta'] == new['path']
    assert (projeto / old['path']).exists()
    assert fm(projeto, old['path'])['substituida_por_arquivo'] == new['path']
    assert fm(projeto, new['path'])['status'] == 'ativa'
    with pytest.raises(ValueError, match='já não está ativa'):
        revisar_decisao(projeto, old['path'], 'revogar', 'Foi cancelada em revisão posterior.')

    other = registrar_decisao(projeto, 'Usar ferramenta X', 'Adotar a ferramenta X no projeto.', forcar=True)
    revoked = revisar_decisao(projeto, other['path'], 'revogar', 'A ferramenta saiu do projeto.')
    assert revoked['status'] == 'revogada' and revoked['substituta'] is None
    assert fm(projeto, other['path'])['status'] == 'revogada'


@pytest.mark.embeddings
def test_dedupe_calibrado(projeto, com_embeddings):
    registrar_decisao(projeto, "Embeddings com e5-large multilíngue", "Trocar para intfloat/multilingual-e5-large.")
    dup = registrar_decisao(projeto, "Modelo de embeddings e5-large", "Trocar o modelo de embeddings para multilingual-e5-large.")
    assert not dup["criada"] and dup["similaridade"] >= 0.85
    registrar_decisao(projeto, "Preço do plano Pro é R$ 97", "Definimos o plano Pro a R$ 97 por mês.")
    mud = registrar_decisao(projeto, "Preço do plano Pro é R$ 147", "Definimos o plano Pro a R$ 147 por mês.")
    assert mud["criada"] and "substitui=" in mud["aviso"]  # mudança, não duplicata
    ok = registrar_decisao(projeto, "Deploy na Vercel", "Hospedar o frontend na Vercel.")
    assert ok["criada"] and "aviso" not in ok


def test_aprendizado(projeto):
    r = registrar_aprendizado(projeto, "SDK MCP v2 renomeou FastMCP",
                              "Na versão 2 do SDK, FastMCP virou MCPServer em mcp.server.mcpserver; código da v1 quebra.")
    assert r["path"].startswith(".atlasbrain/Aprendizados/") and fm(projeto, r["path"])["tipo"] == "aprendizado"


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
    t = captura.ler_conversa(claude)
    assert "[USUÁRIO] Vamos usar SQLite?" in t and "[ASSISTENTE] Sim, SQLite." in t
    assert not any(x in t for x in ("segredo", "saida do ls", "ignorar", "subagente"))

    codex = tmp_path / "x.jsonl"
    codex.write_text("\n".join(json.dumps(x) for x in [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Oi Codex"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Feito."}]}},
    ]))
    t = captura.ler_conversa(codex)
    assert "[USUÁRIO] Oi Codex" in t and "[ASSISTENTE] Feito." in t and "shell" not in t


def test_worker_usa_extrator_e_nao_repete(projeto, tmp_path, monkeypatch):
    tr = tmp_path / "t.jsonl"
    tr.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Decidimos usar Redis para fila. " * 400}}))
    chamadas = []

    def falso(prompt):
        chamadas.append(prompt)
        return {"decisoes": [
                    {"titulo": "Redis para fila", "decisao": "Vamos usar Redis como fila de jobs no lugar do cron.",
                     "motivo": "O cron perdia jobs quando o servidor reiniciava.", "importancia": 4, "durabilidade": 5},
                    {"titulo": "Rodar os testes", "decisao": "Agora vou rodar os testes e ver se passa.",
                     "importancia": 1, "durabilidade": 1}],
                "aprendizados": []}

    monkeypatch.setattr(captura, "_extrair", falso)
    captura.worker(projeto, tr, "s1", str(projeto), "Stop")
    assert list((projeto / ".atlasbrain" / "Decisões").glob("*Redis para fila.md"))
    assert "Data da conversa:" in chamadas[0]
    captura.worker(projeto, tr, "s1", str(projeto), "Stop")  # nada novo desde a última captura
    assert len(chamadas) == 1
    log = (projeto / ".atlasbrain" / "captura.log").read_text()
    assert "Redis para fila" in log
    assert "✗ recusada “Rodar os testes”" in log  # passo de execução não vira nota
    assert not list((projeto / ".atlasbrain" / "Decisões").glob("*Rodar os testes*"))


def test_hook_nao_recursivo_e_nao_trava(projeto, monkeypatch):
    monkeypatch.setenv("ATLASBRAIN_CAPTURANDO", "1")
    r = subprocess.run([sys.executable, "-m", "atlasbrain.cli", "capturar"], input="{}", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout == ""


def test_briefing_prioriza_projeto_atual(projeto):
    registrar_decisao(projeto, "Decisão de outro", "x", projeto="outro", forcar=True)
    registrar_decisao(projeto, "Decisão daqui", "y", projeto=projeto.name, forcar=True)
    b = captura.briefing(projeto, str(projeto))
    assert b.index("Decisão daqui") < b.index("Decisão de outro")
    assert "registrar_decisao" in b


# ------------------------------------------------------------------ portão de relevância

@pytest.mark.parametrize("tipo,titulo,texto,motivo,passa", [
    ("decisao", "Usar SQLite no MVP", "Vamos usar SQLite local em vez de Postgres na primeira versão.", "Menos infra.", True),
    ("decisao", "Preço do plano Pro", "Definimos o plano Pro a R$ 147 por mês a partir de outubro.", "", True),
    ("aprendizado", "SDK MCP v2", "Na versão 2 do SDK do MCP, FastMCP virou MCPServer em mcp.server.mcpserver; código da v1 quebra.", "", True),
    ("decisao", "Rodar os testes", "Agora vou rodar os testes e ver se passa.", "", False),
    ("decisao", "Talvez trocar o banco", "Talvez a gente possa pensar em usar outro banco no futuro.", "", False),
    ("aprendizado", "Corrigi o typo", "Corrigi o typo no README e fiz o commit.", "", False),
    ("decisao", "Melhorar o código", "Precisamos melhorar a qualidade do código de modo geral.", "", False),
    ("aprendizado", "Python é bom", "Python é uma linguagem de programação muito usada.", "", False),
])
def test_portao_de_relevancia(tipo, titulo, texto, motivo, passa):
    from atlasbrain.relevancia import avaliar
    a = avaliar(tipo, titulo, texto, motivo, modo="automatico")
    assert a.aceito is passa, a.resumo()
    if not passa:
        assert a.motivos  # toda recusa diz o porquê


def test_manual_barra_so_o_inutil(projeto):
    r = registrar_decisao(projeto, "ok", "curto")
    assert not r["criada"] and "relevância" in r["motivo"]
    r = registrar_decisao(projeto, "Rodar os testes", "Agora vou rodar os testes e ver se passa.")
    assert not r["criada"]
    r = registrar_decisao(projeto, "Rodar os testes", "Agora vou rodar os testes e ver se passa.", forcar=True)
    assert r["criada"]  # o agente pode insistir


# ------------------------------------------------------------------ atualizar em vez de duplicar

def test_novidades_por_frase():
    from atlasbrain.registro import novidades
    antigo = "# Fila\n\n## Decisão\n\nUsar Redis como fila de jobs. O cron perdia jobs no restart.\n"
    assert novidades(antigo, "Usar Redis como fila de jobs.") == []  # nada novo
    novas = novidades(antigo, "Usar Redis como fila de jobs. O timeout de cada job é 30 s.")
    assert novas == ["O timeout de cada job é 30 s."]  # número que a nota não tinha
    assert novidades(antigo, "Configuração em `lib/queue/redis.ts`.") == ["Configuração em `lib/queue/redis.ts`."]


def test_atualizar_nota_acrescenta_so_o_novo(projeto):
    from atlasbrain.registro import atualizar_nota
    r = registrar_decisao(projeto, "Redis para fila", "Vamos usar Redis como fila de jobs.", motivo="O cron perdia jobs.")
    up = atualizar_nota(projeto, "Redis para fila", "Vamos usar Redis como fila de jobs. Cada job tem timeout de 30 s.",
                        origem="teste", sessao="s9")
    assert up["atualizada"] and up["acrescentado"] == ["Cada job tem timeout de 30 s."]
    texto = (projeto / r["path"]).read_text()
    assert texto.count("## Atualizações") == 1 and "(teste): Cada job tem timeout de 30 s." in texto
    assert "Vamos usar Redis" in texto  # o original continua lá
    meta = fm(projeto, r["path"])
    assert meta["atualizacoes"] == 1 and "s9" in meta["sessoes"]
    assert not atualizar_nota(projeto, "Redis para fila", "Cada job tem timeout de 30 s.")["atualizada"]  # já tem
    assert atualizar_nota(projeto, "Redis para fila", "Workers rodam em 2 réplicas.")["atualizada"]
    assert (projeto / r["path"]).read_text().count("## Atualizações") == 1


@pytest.mark.embeddings
def test_mesmo_assunto_atualiza_em_vez_de_criar(projeto, com_embeddings):
    a = registrar_decisao(projeto, "Embeddings com e5-large multilíngue", "Trocar para intfloat/multilingual-e5-large.",
                          motivo="Mais precisão na busca.")
    b = registrar_decisao(projeto, "Modelo de embeddings e5-large",
                          "Trocar o modelo de embeddings para multilingual-e5-large. Reindexação completa leva 3 min no olax.")
    assert not b["criada"] and b["atualizada"] and b["path"] == a["path"]
    assert any("3 min" in f for f in b["acrescentado"])
    assert len(list((projeto / ".atlasbrain" / "Decisões").glob("*.md"))) == 1


@pytest.mark.embeddings
def test_mudanca_de_valor_e_detectada(projeto, com_embeddings):
    registrar_decisao(projeto, "Réplicas do worker", "Rodar 3 réplicas do worker de fila.", motivo="Aguenta o pico de segunda.")
    manual = registrar_decisao(projeto, "Réplicas do worker", "Rodar 5 réplicas do worker de fila.", motivo="Pico cresceu.")
    assert manual["criada"] and "MUDANÇA" in manual["aviso"]
    auto = registrar_decisao(projeto, "Réplicas do worker de fila", "Rodar 8 réplicas do worker de fila.",
                             motivo="Black Friday dobrou o volume.", modo="automatico",
                             llm={"importancia": 4, "durabilidade": 4})
    assert not auto["criada"] and auto["revisao_necessaria"]
