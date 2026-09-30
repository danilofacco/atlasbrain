"""Compare a frozen Git version and current search against identical SQLite snapshots.

Only aggregate metrics and anonymous case positions are emitted. Existing private
labels are read locally, never copied into the public result. Scoped derivatives
use the reviewed answer's folder/type; they test scope behavior, not independent
natural-language generalization. No live indexes or project files are changed.
"""
import argparse
import importlib.util
import json
import sqlite3
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from atlasbrain import config, db, embed
from atlasbrain.search import Searcher

ROOT = Path(__file__).resolve().parents[1]


def baseline(ref, directory):
    sha = subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options', ref+'^{commit}'], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(['git', 'show', sha+':atlasbrain/search.py'], cwd=ROOT, text=True)
    file = directory/'search_before.py'; file.write_text(source)
    spec = importlib.util.spec_from_file_location('atlasbrain._filters_baseline', file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return sha, module.Searcher


def metrics(rows):
    ranks=[row['rank'] for row in rows]
    samples=[value for row in rows for value in row['samples_ms']]
    times=sorted(samples)
    count=len(rows)
    return dict(cases=count, top1=sum(r==1 for r in ranks)/count,
                top3=sum(r is not None and r<=3 for r in ranks)/count,
                mrr=sum(1/r for r in ranks if r)/count,
                no_results=sum(row['empty'] for row in rows),
                p50_ms=statistics.median(times), p95_ms=times[min(len(times)-1,int(len(times)*.95))])


def compare(con, cases, Before, repeats):
    searchers={'before':Before(con),'after':Searcher(con)}
    data={variant:[] for variant in searchers}
    # Initialize vector/ranking caches and model outside warmed-query timings.
    cold={}
    for name,searcher in searchers.items():
        start=time.perf_counter(); searcher.search(cases[0]['query'])
        cold[name]=(time.perf_counter()-start)*1000
    for index,case in enumerate(cases):
        outputs={variant:[] for variant in searchers}
        samples={variant:[] for variant in searchers}
        for repeat in range(repeats):
            order=list(searchers) if (index+repeat)%2==0 else list(reversed(searchers))
            for name in order:
                start=time.perf_counter()
                hits=searchers[name].search(case['query'],limit=10)
                samples[name].append((time.perf_counter()-start)*1000)
                paths=[hit['path'] for hit in hits]
                if repeat and outputs[name]!=paths:
                    raise ValueError('Search order changed during the frozen-index measurement')
                outputs[name]=paths
        for name,paths in outputs.items():
            rank=next((i+1 for i,path in enumerate(paths) if path in case['expected']),None)
            data[name].append(dict(rank=rank,empty=not paths,samples_ms=samples[name]))
    changes=[dict(case=i+1,before=b['rank'],after=a['rank']) for i,(b,a) in enumerate(zip(data['before'],data['after'])) if b['rank']!=a['rank']]
    return dict(metrics={name:metrics(rows) for name,rows in data.items()},rank_changes=changes,
                warmup_ms=cold,repeats=repeats)


def load_cases(vault):
    file=vault/'.atlasbrain'/'benchmark.jsonl'
    if file.is_file():
        return [json.loads(line) for line in file.read_text().splitlines() if line.strip() and not line.startswith('//')]
    file=vault/'.atlasbrain'/'.editor-backups'/'benchmark-latest.json'
    if file.is_file():
        return json.loads(file.read_text())['casos']
    raise ValueError('No reviewed benchmark labels for '+vault.name)


def evaluate_project(vault, directory, Before, repeats):
    source=sqlite3.connect((vault/'.atlasbrain'/'index.db').as_uri()+'?mode=ro',uri=True)
    con=sqlite3.connect(directory/(vault.name+'.db')); con.row_factory=sqlite3.Row
    try:
        source.backup(con)
    finally:
        source.close()
    try:
        revision=db.get_meta(con,'rev')
        queries=load_cases(vault)
        cohorts={'original':[],'folder_scoped':[],'type_scoped':[]}
        for item in queries:
            query=item['pergunta']; expected=item['esperado']
            for path in expected:
                if not con.execute('SELECT 1 FROM notes WHERE path=?',(path,)).fetchone():
                    raise ValueError('A reviewed expected path is missing from '+vault.name+'; refresh labels first')
            cohorts['original'].append(dict(query=query,expected=expected))
            parent=Path(expected[0]).parent.as_posix()
            if parent!='.':
                scoped=[path for path in expected if path.startswith(parent+'/')]
                cohorts['folder_scoped'].append(dict(query=query+' pasta:'+json.dumps(parent,ensure_ascii=False),expected=scoped))
            row=con.execute('SELECT kind,frontmatter FROM notes WHERE path=?',(expected[0],)).fetchone()
            tipo=json.loads(row['frontmatter'] or '{}').get('tipo') or row['kind']
            matching=[]
            for path in expected:
                meta=con.execute('SELECT kind,frontmatter FROM notes WHERE path=?',(path,)).fetchone()
                if tipo in (meta['kind'],json.loads(meta['frontmatter'] or '{}').get('tipo')):
                    matching.append(path)
            cohorts['type_scoped'].append(dict(query=query+' tipo:'+str(tipo),expected=matching))
        results={}
        for name,cases in cohorts.items():
            if cases:
                print('  '+name+': '+str(len(cases))+' cases',flush=True)
                results[name]=compare(con,cases,Before,repeats)
        return dict(project=vault.name,revision=revision,index_stable=revision==db.get_meta(con,'rev'),
                    files=con.execute('SELECT COUNT(*) FROM notes').fetchone()[0],
                    vectors=con.execute('SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL').fetchone()[0],
                    cohorts=results)
    finally:
        con.close()


def insert(con,path,text,*,fm=None,tag=None):
    nid=con.execute('INSERT INTO notes(path,title,kind,frontmatter,mtime,size) VALUES(?,?,?,?,?,?)',
                    (path,'Entry','nota',json.dumps(fm or {}),1790726400,len(text))).lastrowid
    cid=con.execute('INSERT INTO chunks(note_id,ord,heading,text,title) VALUES(?,?,?,?,?)',(nid,0,'',text,'Entry')).lastrowid
    con.execute('INSERT INTO chunks_fts(rowid,title,heading,text) VALUES(?,?,?,?)',(cid,'Entry','',text))
    if tag: con.execute('INSERT INTO tags(note_id,tag) VALUES(?,?)',(nid,tag))


def synthetic(directory,Before,repeats):
    definitions=[
        ('folder','needle pasta:scope'), ('tag','needle tag:chosen'),
        ('type','needle tipo:decisao'), ('status','needle status:ativa'),
        ('date','needle desde:2026-09'), ('exclusion','needle -legacy'),
        ('phrase','needle "approved answer"'),
        ('combined','needle pasta:scope tag:chosen tipo:decisao status:ativa desde:2026-09 -legacy "approved answer"'),
        ('filters_only','pasta:scope tipo:decisao'), ('plural_alias','pasta:scope tipo:decisões'),
    ]
    out={}
    old_enabled=embed.EMBED_ENABLED; embed.EMBED_ENABLED=False
    try:
        for name,query in definitions:
            vault=directory/name; vault.mkdir(); con=db.connect(vault)
            try:
                for i in range(205):
                    insert(con,f'outside/{i:03}.md','needle approved separated answer legacy',fm={'tipo':'aprendizado','status':'revogada','data':'2025-01-01'})
                insert(con,'scope/answer.md','needle approved answer '+'filler '*50,
                       fm={'tipo':'decisao','status':'ativa','data':'2026-09-30'},tag='chosen')
                con.commit()
                result=compare(con,[dict(query=query,expected=['scope/answer.md'])],Before,repeats)
                out[name]=result
                for engine in (Before(con),Searcher(con)):
                    if engine.search('needle pasta:missing'):
                        raise AssertionError('An explicit nonexistent scope leaked results')
            finally: con.close()
    finally: embed.EMBED_ENABLED=old_enabled
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vaults',nargs='*',type=Path)
    parser.add_argument('--before-ref',default='8cccda5')
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.repeats<1: parser.error('--repeats must be positive')
    with tempfile.TemporaryDirectory(prefix='atlasbrain-filter-bench-') as temp:
        directory=Path(temp); sha,Before=baseline(args.before_ref,directory)
        report=dict(baseline_commit=sha,model=config.EMBED_MODEL,
                    timings='Warmed, interleaved queries on frozen SQLite copies; initialization excluded. Token cost not measured.',
                    scope_labels='Folder/type queries are derivatives of reviewed labels, not independent natural-language validation.',
                    synthetic=synthetic(directory,Before,args.repeats),projects=[])
        if args.vaults and embed.enabled():
            from fastembed import TextEmbedding
            # Require the installed model cache. A comparison must not fetch a
            # model or upload source; pass --no-embed via environment if absent.
            embed._model=TextEmbedding(model_name=config.EMBED_MODEL,cache_dir=str(config.MODEL_CACHE),local_files_only=True)
        for vault in args.vaults:
            print('Comparing '+vault.name+'...',flush=True)
            report['projects'].append(evaluate_project(vault.resolve(),directory,Before,args.repeats))
        text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(text)
        print(text)


if __name__=='__main__':main()
