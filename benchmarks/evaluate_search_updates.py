"""Compare ranking changes against existing labelled queries, in an isolated vault."""
import json
import tempfile
import time
from pathlib import Path
from atlasbrain import config, embed
from atlasbrain.db import connect
from atlasbrain.indexer import index_vault
from atlasbrain.search import Searcher


def main():
    fixture = json.loads(Path('benchmarks/search_fixture.json').read_text())
    root = Path(tempfile.mkdtemp(prefix='atlasbrain-search-regression-'))
    config.REGISTRY = root/'registry.json'; config.GLOBAL_BRAIN = root/'global'; config._registered.clear()
    vault = root/'vault';vault.mkdir()
    for d in fixture['documents']:
        p = vault/d['path'];p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('# '+d['title']+'\n\n'+d['text']+'\n')
    embed.EMBED_ENABLED = True
    index_vault(vault, quiet=True)
    con = connect(vault);searcher=Searcher(con)
    queries=[q for q in fixture['queries'] if q['split']=='test' and q['expected']]
    results={}
    for name,signals in [('previous',('fts','vec','struct','demote')),('updated',('fts','vec','struct','demote','exact','status'))]:
        cases=[]
        for q in queries:
            t=time.perf_counter(); paths=[r['path'] for r in searcher.search(q['query'], signals=signals)]
            rank=next((i+1 for i,p in enumerate(paths) if p in q['expected']),0)
            cases.append(dict(id=q['id'],rank=rank,paths=paths,ms=(time.perf_counter()-t)*1000))
        results[name]={'top1':sum(c['rank']==1 for c in cases)/len(cases),
                       'mrr':sum(1/c['rank'] if c['rank'] else 0 for c in cases)/len(cases),'cases':cases}
    con.close()
    output=Path('benchmarks/results/search-updates-2026-09-29.json')
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:{m:r[m] for m in ('top1','mrr')} for k,r in results.items()}))

if __name__=='__main__':main()
