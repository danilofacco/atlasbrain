"""Benchmark de busca: perguntas com a resposta certa conhecida, medindo cada sinal isolado e combinado.

Arquivo de perguntas (JSONL), por padrão em <projeto>/.atlasbrain/benchmark.jsonl:
    {"pergunta": "onde o boleto é enviado?", "esperado": ["lib/ai/functions/clinicorp/boleto.ts"], "tipo": "conceito"}
`esperado` aceita vários caminhos (qualquer um conta como acerto); `tipo` só agrupa o relatório.
"""

import json
import statistics
import time
from pathlib import Path

from .consultas import achar_arquivos
from .db import connect
from .search import Searcher

CONFIGS = {
    "híbrido (atual)": ("buscar", ("fts", "vec", "struct", "demote", "exact", "status")),
    "híbrido sem rebaixar testes": ("buscar", ("fts", "vec", "struct")),
    "palavra + significado": ("buscar", ("fts", "vec")),
    "só palavra (FTS)": ("buscar", ("fts",)),
    "só significado": ("buscar", ("vec",)),
    "só símbolo/caminho": ("buscar", ("struct",)),
    "tool arquivos": ("arquivos", None),
}


def carregar(f: Path) -> list[dict]:
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("//")]


def _rank(resultados: list[str], esperado: list[str]) -> int | None:
    for i, p in enumerate(resultados):
        if p in esperado:
            return i + 1
    return None


def rodar(vault: Path, perguntas: list[dict], configs=None, k: int = 10) -> dict:
    con = connect(vault)
    s = Searcher(con)
    s.search("aquecimento")  # carrega vetores e modelo fora da medição
    out = {}
    for nome, (tool, sinais) in (configs or CONFIGS).items():
        linhas = []
        for q in perguntas:
            t0 = time.perf_counter()
            if tool == "arquivos":
                res = [r["path"] for r in achar_arquivos(con, q["pergunta"], k)]
            else:
                res = [r["path"] for r in s.search(q["pergunta"], limit=k, sinais=sinais)]
            ms = (time.perf_counter() - t0) * 1000
            linhas.append({**q, "rank": _rank(res, q["esperado"]), "ms": ms, "top3": res[:3]})
        out[nome] = linhas
    con.close()
    return out


def metricas(linhas: list[dict]) -> dict:
    n = len(linhas) or 1
    r = [l["rank"] for l in linhas]
    ms = sorted(l["ms"] for l in linhas)
    return {
        "top1": sum(1 for x in r if x == 1) / n,
        "top3": sum(1 for x in r if x and x <= 3) / n,
        "top5": sum(1 for x in r if x and x <= 5) / n,
        "mrr": sum(1 / x for x in r if x) / n,
        "p50_ms": statistics.median(ms) if ms else 0,
        "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else 0,
    }


def relatorio(res: dict, detalhes: bool = False) -> str:
    pct = lambda x: f"{x * 100:5.1f}%"
    n = len(next(iter(res.values())))
    out = [f"Benchmark: {n} perguntas\n",
           f"{'configuração':24} {'top1':>7} {'top3':>7} {'top5':>7} {'MRR':>6} {'p50':>7} {'p95':>7}"]
    for nome, linhas in res.items():
        m = metricas(linhas)
        out.append(f"{nome:24} {pct(m['top1'])} {pct(m['top3'])} {pct(m['top5'])} {m['mrr']:6.3f} "
                   f"{m['p50_ms']:5.0f}ms {m['p95_ms']:5.0f}ms")
    base = next(iter(res))
    tipos = sorted({l.get("tipo", "-") for l in res[base]})
    if len(tipos) > 1:
        out.append(f"\nPor tipo de pergunta ({base}):")
        for t in tipos:
            m = metricas([l for l in res[base] if l.get("tipo", "-") == t])
            out.append(f"  {t:14} top3 {pct(m['top3'])}  MRR {m['mrr']:.3f}  ({sum(1 for l in res[base] if l.get('tipo', '-') == t)} perguntas)")
    erros = [l for l in res[base] if not l["rank"] or l["rank"] > 3]
    if erros:
        out.append(f"\nFora do top 3 no {base} ({len(erros)}):")
        for l in erros:
            out.append(f"  - {l['pergunta']}  → esperado {l['esperado'][0]} (posição {l['rank'] or '>10'})")
            if detalhes:
                out.append(f"      veio: {', '.join(l['top3'])}")
    return "\n".join(out)


def avaliar_projeto(vault, perguntas=None, tokens=2000):
    """Labelled project evaluation. Reports stay local; never creates labels from search results."""
    from types import SimpleNamespace
    from . import inteligencia, editor
    from .db import get_meta
    if perguntas is None:
        file=vault/'.atlasbrain'/'benchmark.jsonl'
        if not file.is_file():
            raise ValueError('Crie benchmark.jsonl com pergunta e esperado, ou forneça perguntas rotuladas.')
        perguntas=carregar(file)
    if not isinstance(perguntas,list) or not 1<=len(perguntas)<=100:
        raise ValueError('Informe de 1 a 100 perguntas')
    con=connect(vault)
    try:
        for q in perguntas:
            if not isinstance(q,dict) or not isinstance(q.get('pergunta'),str) or not q['pergunta'].strip() or not isinstance(q.get('esperado'),list) or not q['esperado']:
                raise ValueError('Cada pergunta precisa de texto e caminhos esperados revisados')
            for path in q['esperado']:
                if not isinstance(path,str) or not con.execute('SELECT 1 FROM notes WHERE path=?',(path,)).fetchone():
                    raise ValueError('Resposta esperada ausente do índice: '+str(path))
        revision=get_meta(con,'rev');searcher=Searcher(con);searcher.search('aquecimento')
        ctx=SimpleNamespace(con=con,vault=vault,searcher=searcher)
        cases=[]
        for q in perguntas:
            start=time.perf_counter();hits=searcher.search(q['pergunta']);search_ms=(time.perf_counter()-start)*1000
            candidates=searcher.search(q['pergunta'],limit=50)
            rank_50=_rank([r['path'] for r in candidates],q['esperado'])
            start=time.perf_counter();context=inteligencia.budget_report(inteligencia.task_context(ctx,q['pergunta']),tokens)
            cases.append({**q,'rank':_rank([r['path'] for r in hits],q['esperado']),'ms':search_ms,
                          'rank_50':rank_50,'diagnostico':'ausente_dos_candidatos' if rank_50 is None else 'ordenacao' if rank_50>3 else 'top_3',
                          'contexto_ms':(time.perf_counter()-start)*1000,'contexto_caracteres':len(context),
                          'tokens_estimados':(len(context)+2)//3,'top3':[r['path'] for r in hits[:3]]})
        report={'projeto':vault.name,'revisao':revision,'indice_estavel':revision==get_meta(con,'rev'),
                'metricas':{**metricas(cases),'recuperacao_50':sum(c['rank_50'] is not None for c in cases)/len(cases)},
                'diagnostico':{key:sum(c['diagnostico']==key for c in cases) for key in ('top_3','ordenacao','ausente_dos_candidatos')},
                'casos':cases,'tokens_sao_estimativa':True}
        editor._atomic(editor._cache(vault)/'benchmark-latest.json',json.dumps(report,ensure_ascii=False,indent=2).encode(),mode=0o600)
        return report
    finally:
        con.close()
