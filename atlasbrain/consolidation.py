"""Bounded, local, extractive memory consolidation. Sources are never deleted."""
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

from . import editor
from .config import notes_dir
from .db import connect
from .indexer import index_vault
from .parse import split_frontmatter

MINIMUM = 40
BATCH = 6


def coverage(con):
    """Only current summaries cover their sources; changed/deleted sources invalidate them."""
    summaries = con.execute("SELECT path, frontmatter FROM notes WHERE json_extract(frontmatter, '$.tipo')='consolidacao'").fetchall()
    if not summaries:
        return {}, set()
    rows = {r['path']: r for r in con.execute('SELECT path, hash FROM notes')}
    covered, stale = {}, set()
    for row in summaries:
        path = row['path']
        fm = json.loads(row['frontmatter'] or '{}')
        sources = fm.get('fontes_consolidadas')
        if fm.get('tipo') != 'consolidacao' or not isinstance(sources, list) or not sources:
            continue
        valid = all(isinstance(s, dict) and s.get('path') in rows and rows[s['path']]['hash'] == s.get('hash') for s in sources)
        if valid:
            for source in sources:
                covered[source['path']] = path
        else:
            stale.add(path)
    return covered, stale


def plan(vault, con, minimum=MINIMUM):
    covered, stale = coverage(con)
    groups = defaultdict(list)
    count = 0
    for row in con.execute("SELECT path, title, kind, frontmatter FROM notes WHERE kind='nota' ORDER BY path"):
        path = row['path']
        try:
            file, _ = editor._file(vault, path)
        except editor.EditError:
            continue
        if not file.is_file():
            continue
        fm = json.loads(row['frontmatter'] or '{}')
        brain_prefix=notes_dir(vault).relative_to(vault).as_posix()
        if brain_prefix != '.' and not path.startswith(brain_prefix+'/') and not fm.get('tags') and fm.get('tipo') not in ('nota','decisao','aprendizado'):
            continue
        if fm.get('tipo') in ('consolidacao', 'vinculo', 'importacao') or fm.get('status', 'ativa') != 'ativa':
            continue
        count += 1
        if path in covered:
            continue
        # Explicit topic tags first; generic category tags cannot prove a shared topic.
        tags = fm.get('tags') or []
        if isinstance(tags, str):
            tags = [tags]
        tags = sorted(str(t).lstrip('#').casefold() for t in tags if str(t).lstrip('#').casefold() not in ('decisao','aprendizado','nota'))
        topic = 'tag:'+tags[0] if tags else 'pasta:'+str(Path(path).parent)
        groups[(str(fm.get('projeto') or ''), topic)].append({'path':path, 'title':row['title']})
    candidates = []
    for (project, topic), sources in sorted(groups.items()):
        if len(sources) < 3:
            continue
        sources = sources[:BATCH]
        identity = hashlib.sha256('\n'.join(s['path'] for s in sources).encode()).hexdigest()[:20]
        candidates.append({'grupo':identity, 'projeto':project, 'tema':topic, 'fontes':sources,
                           'aviso':'Agrupamento por metadados; não prova equivalência entre as notas.'})
    return {'total_notas':count, 'fontes_cobertas':len(covered), 'sinteses_atuais':sorted(set(covered.values())), 'resumos_desatualizados':sorted(stale),
            'automatico_indicado':count>=minimum, 'limiar':minimum, 'grupos':candidates[:10],
            'mais_grupos':len(candidates)>10}


def prepare(vault, con, group):
    candidate = next((g for g in plan(vault,con)['grupos'] if g['grupo']==group), None)
    if not candidate:
        if not isinstance(group,str) or not re.fullmatch(r'[0-9a-f]{20}',group):
            raise editor.EditError('Grupo inválido')
        path=_summary_path(vault, group)
        try:
            existing=editor.read(vault,path)
        except editor.EditError:
            raise editor.EditError('Grupo indisponível; consulte o plano atualizado.',409)
        fm=split_frontmatter(existing['content'])[0]
        refs=fm.get('fontes_consolidadas')
        if fm.get('tipo')!='consolidacao' or not isinstance(refs,list) or not 3<=len(refs)<=BATCH:
            raise editor.EditError('Síntese inválida',409)
        candidate={'grupo':group,'tema':fm.get('tema','pasta:memoria'),'projeto':fm.get('projeto',''),
                   'fontes':[{'path':r['path'],'title':Path(r['path']).stem} for r in refs]}
    sources=[]
    for item in candidate['fontes']:
        current=editor.read(vault,item['path'])
        sources.append({**item,'revision':current['revision'],'content':current['content']})
    # No silent truncation: the MCP can select a smaller set after reading the plan.
    if sum(len(s['content']) for s in sources)>40000:
        raise editor.EditError('Grupo maior que 40 mil caracteres; use ler por seção para revisar as fontes.',413)
    return {**candidate,'fontes':sources}


def extract(content, budget=700):
    body=split_frontmatter(content)[1]
    paragraphs=[p.strip() for p in re.split(r'\n\s*\n',body) if p.strip() and not p.strip().startswith('#')]
    out=[];used=0
    for p in paragraphs:
        if used+len(p)>budget:
            # Never cut a sentence and imply the excerpt is complete.
            continue
        out.append(p);used+=len(p)
    return '\n\n'.join(out) if out else 'Consulte a fonte: o conteúdo não cabe no limite de extração.'


def consolidate(vault, group, summary=None, revisions=None):
    """One atomic summary write; original files, links and decisions remain intact."""
    with editor._lock(vault):
        con=connect(vault)
        try:
            bundle=prepare(vault,con,group)
        finally:
            con.close()
        sources=bundle['fontes']
        if summary is not None:
            if not isinstance(summary,str) or not 40<=len(summary)<=12000:
                raise editor.EditError('Resumo deve conter entre 40 e 12 mil caracteres.')
            if revisions != {s['path']:s['revision'] for s in sources}:
                raise editor.EditError('As fontes mudaram ou faltam revisões; prepare o grupo novamente.',409)
        mode='agente' if summary is not None else 'extrativo'
        if summary is None:
            summary='\n\n'.join('### '+s['title']+'\n\n'+extract(s['content']) for s in sources)
        title='Síntese · '+bundle['tema'].split(':',1)[-1]
        path=_summary_path(vault, group)
        source_meta=[{'path':s['path'],'hash':hashlib.sha1(s['content'].encode()).hexdigest(),
                      'revision':s['revision']} for s in sources]
        fm={'title':title,'tipo':'consolidacao','modo':mode,'projeto':bundle['projeto'],'tema':bundle['tema'],
            'fontes_consolidadas':source_meta,'tags':['sintese']}
        content='---\n'+yaml.safe_dump(fm,allow_unicode=True,sort_keys=False)+'---\n\n# '+title+'\n\n'
        content+='> Síntese '+mode+'. Consulte as fontes para detalhes, ressalvas e contexto. Divergências não são resolvidas automaticamente.\n\n'
        content+=summary.strip()+'\n\n## Fontes\n\n'+'\n'.join('- [['+s['path']+']]' for s in sources)+'\n'
        # Recheck all source bytes immediately before writing the summary.
        for source in sources:
            if editor.read(vault,source['path'])['revision']!=source['revision']:
                raise editor.EditError('Uma fonte mudou durante a consolidação.',409)
        target=vault/path
        if target.exists():
            current=editor.read(vault,path)
            current_fm=split_frontmatter(current['content'])[0]
            if mode=='extrativo' and current_fm.get('modo')=='agente':
                raise editor.EditError('Síntese do agente exige revisão explícita com resumo e revisões.',409)
            if current_fm.get('tipo')!='consolidacao':
                raise editor.EditError('Destino ocupado por outro arquivo.',409)
            saved=editor.save(vault,path,content,current['revision'])
        else:
            saved=editor.save(vault,path,content,create=True)
    stats=index_vault(vault,quiet=True,wait_timeout=5)
    return {'path':saved['path'],'modo':mode,'fontes':len(sources),'originais_preservados':True,'indice':stats}


def automatic(vault):
    con=connect(vault)
    try:
        state=plan(vault,con)
    finally:
        con.close()
    if not state['automatico_indicado'] or not state['grupos']:
        return {'consolidado':False,'total_notas':state['total_notas'],'limiar':MINIMUM}
    # At most one small group per MCP write; no background model/process.
    for group in state['grupos']:
        try:
            return {'consolidado':True,**consolidate(vault,group['grupo'])}
        except editor.EditError as exc:
            if exc.status != 413:
                raise
    return {'consolidado':False,'motivo':'Grupos excedem o orçamento automático; revisão por seções necessária.'}


def refresh_queue(vault, execute=False):
    """Rebuild the queue from indexed provenance. Never overwrite agent-authored summaries."""
    con=connect(vault)
    try:
        _,stale=coverage(con)
        items=[]
        for path in sorted(stale):
            row=con.execute('SELECT frontmatter FROM notes WHERE path=?',(path,)).fetchone()
            fm=json.loads(row['frontmatter'] or '{}')
            refs=fm.get('fontes_consolidadas',[])
            missing=[]
            for ref in refs:
                source_path=ref.get('path') if isinstance(ref,dict) else None
                try:
                    file,_=editor._file(vault,source_path)
                    if not file.is_file():
                        missing.append(source_path)
                except (editor.EditError,TypeError):
                    missing.append(str(source_path))
            items.append({'path':path,'grupo':Path(path).stem,'modo':fm.get('modo'),
                          'estado':'fonte_ausente' if missing else 'revisao_agente' if fm.get('modo')!='extrativo' else 'pendente',
                          'fontes_ausentes':missing})
    finally:
        con.close()
    updated=None
    if execute:
        for item in items:
            if item['estado']=='pendente':
                try:
                    updated=consolidate(vault,item['grupo'])
                    item['estado']='atualizado'
                except (editor.EditError,OSError) as exc:
                    item['estado']='bloqueado';item['erro']=str(exc)
                break
    return {'fila':items,'atualizado':updated,'aviso':'Sínteses do agente exigem revisão; nunca são substituídas por extração automática.'}


def _summary_path(vault, group):
    current = notes_dir(vault) / 'Summaries' / (group + '.md')
    legacy = notes_dir(vault) / 'Sinteses' / (group + '.md')
    return (legacy if not current.exists() and legacy.exists() else current).relative_to(vault).as_posix()
