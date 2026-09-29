"""Revision-checked Markdown moves with link rewriting and a durable recovery journal."""
import hashlib
import json
import posixpath
import re
import uuid
from urllib.parse import unquote, quote, urlsplit

import yaml

from . import editor
from .indexer import index_vault
from .parse import FENCE_RE, INLINE_CODE_RE, WIKILINK_RE, split_frontmatter

# Include images as well as ordinary relative Markdown links.
MD = re.compile(r'!?\[[^\]\n]*\]\((<[^>]+>|[^)\s]+)(?:\s+"[^"]*")?\)')


def rewrite(text, source, destination, old, new, wiki_targets):
    blocked=[(m.start(),m.end()) for regex in (FENCE_RE,INLINE_CODE_RE) for m in regex.finditer(text)]
    def code(m):
        return any(a<=m.start()<b for a,b in blocked)
    edits=[]
    for m in WIKILINK_RE.finditer(text):
        if not code(m) and m[1].strip().casefold() in wiki_targets:
            edits.append((m.start(1),m.end(1),new))
    for m in MD.finditer(text):
        if code(m):
            continue
        raw=m[1].strip('<>');url=urlsplit(raw)
        if url.scheme or url.netloc or not url.path:
            continue
        target=unquote(url.path)
        full=posixpath.normpath(posixpath.join(posixpath.dirname(source),target)) if not target.startswith('/') else target.lstrip('/')
        if full in (old,posixpath.splitext(old)[0]):
            full=new
        elif source==destination:
            continue
        target=('/'+full) if target.startswith('/') else posixpath.relpath(full,posixpath.dirname(destination) or '.')
        encoded=quote(target,safe='/.-_~')+('?' + url.query if url.query else '')+('#'+url.fragment if url.fragment else '')
        if m[1].startswith('<'):
            encoded='<'+encoded+'>'
        edits.append((m.start(1),m.end(1),encoded))
    for a,b,value in sorted(edits,reverse=True):
        text=text[:a]+value+text[b:]
    return text


def plan(vault, con, old, new):
    source,old=editor._file(vault,old)
    target,new=editor._file(vault,new)
    if old==new or target.exists():
        raise editor.EditError('O destino deve ser um caminho novo e ainda não ocupado.',409)
    row=con.execute('SELECT id,title FROM notes WHERE path=? AND kind=\'nota\'',(old,)).fetchone()
    if not row:
        raise editor.EditError('Nota não encontrada no índice',404)
    by_path, by_noext, by_stem, by_title = {}, {}, {}, {}
    for n in con.execute('SELECT id,path,title,aliases FROM notes ORDER BY length(path)'):
        name=n['path'].casefold()
        by_path.setdefault(name,n['id']);by_noext.setdefault(posixpath.splitext(name)[0],n['id'])
        by_stem.setdefault(posixpath.splitext(posixpath.basename(name))[0],n['id'])
        by_title.setdefault((n['title'] or '').casefold(),n['id'])
        for alias in json.loads(n['aliases'] or '[]'):
            by_title.setdefault(alias.casefold(),n['id'])
    def resolves_old(target):
        raw=target.strip().casefold();short=raw.removesuffix('.md')
        found=by_path.get(raw) or by_noext.get(short) or by_stem.get(posixpath.basename(short)) or by_title.get(short)
        return found==row['id']
    changes=[];total=0
    for note in con.execute("SELECT path FROM notes WHERE kind='nota' ORDER BY path"):
        path=note['path'];snapshot=editor.read(vault,path)
        text=snapshot['content'];dest=new if path==old else path
        wiki={m[1].strip().casefold() for m in WIKILINK_RE.finditer(text) if resolves_old(m[1])}
        updated=rewrite(text,path,dest,old,new,wiki)
        fm,body=split_frontmatter(updated)
        changed_fm=False
        # Graph records and consolidation provenance also contain file paths.
        for key in ('origem_arquivo','destino_arquivo'):
            if fm.get(key)==old:
                fm[key]=new;changed_fm=True
        refs=fm.get('fontes_consolidadas')
        if isinstance(refs,list):
            for ref in refs:
                if isinstance(ref,dict) and ref.get('path')==old:
                    ref['path']=new;changed_fm=True
        if path==old and not fm.get('title') and not fm.get('titulo'):
            fm['title']=row['title'];changed_fm=True
        if changed_fm:
            updated='---\n'+yaml.safe_dump(fm,allow_unicode=True,sort_keys=False)+'---\n'+body
        if path==old and not fm.get('atlasbrain_id'):
            # Deterministic during preview; stable across future moves.
            identity=str(uuid.uuid5(uuid.NAMESPACE_URL,str(vault.resolve())+'\0'+old+'\0'+snapshot['revision']))
            if updated.startswith('---\n'):
                updated='---\natlasbrain_id: '+identity+'\n'+updated[4:]
            else:
                updated='---\natlasbrain_id: '+identity+'\n---\n\n'+updated
        if updated!=text or dest!=path:
            total+=len(text)+len(updated)
            if total>4_000_000:
                raise editor.EditError('Plano maior que 4 milhões de caracteres; divida a operação.',413)
            changes.append({'path':path,'destino':dest,'revision':snapshot['revision'],'antes':text,'depois':updated})
    if not any(c['path']==old for c in changes):
        raise editor.EditError('Origem indisponível',409)
    digest=hashlib.sha256(json.dumps(changes,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'de':old,'para':new,'revisao_plano':digest,'note_id':row['id'],'alteracoes':changes,
            'escopo':'Notas Markdown indexadas; conteúdo de blocos de código não é reescrito.'}


def public_plan(plan):
    return {**{k:v for k,v in plan.items() if k not in ('alteracoes','note_id')},
            'arquivos_afetados':[{'path':c['path'],'destino':c['destino']} for c in plan['alteracoes']]}


def apply(vault, con, old, new, expected):
    with editor._lock(vault):
        prepared=plan(vault,con,old,new)
        if expected!=prepared['revisao_plano']:
            raise editor.EditError('O plano mudou. Gere uma nova prévia antes de renomear.',409)
        changes=prepared['alteracoes']
        journal=editor._cache(vault)/('rename-'+uuid.uuid4().hex+'.json')
        record={'estado':'pendente',**prepared}
        editor._atomic(journal,json.dumps(record,ensure_ascii=False).encode(),mode=0o600,create=True)
        moved=next(c for c in changes if c['path']==old)
        # New destination first: interrupted operations keep old and new paths available.
        try:
            editor.save(vault,new,moved['depois'],create=True)
            editor._snapshot(vault,new,moved['antes'].encode())
            for change in changes:
                if change['path']!=old:
                    editor.save(vault,change['path'],change['depois'],change['revision'])
            if editor.read(vault,old)['revision']!=moved['revision']:
                raise editor.EditError('A origem mudou durante a operação; ambos os caminhos foram preservados. Registro: '+str(journal),409)
            source,_=editor._file(vault,old)
            source.unlink()
            con.execute('UPDATE notes SET path=? WHERE id=?',(new,prepared['note_id']))
            con.commit()
            record['estado']='concluido'
            editor._atomic(journal,json.dumps(record,ensure_ascii=False).encode(),mode=0o600)
        except Exception as exc:
            raise editor.EditError('Renomeação interrompida; confira os arquivos antes de repetir. Registro de recuperação: '+str(journal)+'; '+str(exc),409) from exc
    result=public_plan(prepared)
    result.update(renomeado=True,indice=index_vault(vault,quiet=True,esperar=5))
    return result


def recovery(vault, con, operation_id=None, action=None, expected=None):
    """Inspect or resume/revert a journal only when all files match known states."""
    root=editor._cache(vault)
    if operation_id is None:
        items=[]
        for file in sorted(root.glob('rename-*.json')):
            if file.is_symlink():
                continue
            data=json.loads(file.read_text())
            if data.get('estado') not in ('concluido','revertido'):
                items.append({'id':file.stem,'de':data['de'],'para':data['para'],'estado':data['estado']})
        return {'operacoes':items}
    if not re.fullmatch(r'rename-[0-9a-f]{32}',operation_id):
        raise editor.EditError('Operação inválida')
    if action not in (None,'concluir','reverter'):
        raise editor.EditError('Ação inválida')
    with editor._lock(vault):
        file=root/(operation_id+'.json')
        if file.is_symlink() or not file.is_file():
            raise editor.EditError('Operação não encontrada',404)
        data=json.loads(file.read_text());changes=data['alteracoes']
        old,new=data['de'],data['para']
        snapshots={};conflicts=[]
        moved=next(c for c in changes if c['path']==old)
        for path in dict.fromkeys([old,new]+[c['path'] for c in changes]):
            target,_=editor._file(vault,path)
            snapshots[path]=editor.read(vault,path) if target.exists() else None
        for c in changes:
            current=snapshots[c['path']]
            allowed=(c['antes'],c['depois']) if c['path']!=old else (c['antes'],)
            if current is None and c['path']!=old or current and current['content'] not in allowed:
                conflicts.append(c['path'])
        if snapshots[new] and snapshots[new]['content']!=moved['depois']:
            conflicts.append(new)
        if not snapshots[old] and not snapshots[new]:
            conflicts.append(old)
        token=hashlib.sha256(json.dumps([data,{p:s['revision'] if s else None for p,s in snapshots.items()}],sort_keys=True).encode()).hexdigest()
        report={'id':operation_id,'estado':data['estado'],'de':old,'para':new,'revisao_recuperacao':token,'conflitos':conflicts}
        if action is None:
            return report
        if expected!=token or conflicts:
            raise editor.EditError('Recuperação bloqueada: revisão mudou ou há edições externas. Gere uma nova inspeção.',409)
        if data['estado'] in ('concluido','revertido'):
            raise editor.EditError('Operação já encerrada',409)
        data['estado']='recuperando_'+action
        editor._atomic(file,json.dumps(data,ensure_ascii=False).encode(),mode=0o600)
        def put(path,text):
            current=snapshots[path]
            if not current or current['content']!=text:
                editor.save(vault,path,text,current['revision'] if current else None,create=current is None)
        def remove(path):
            current=snapshots[path]
            if current:
                if editor.read(vault,path)['revision']!=current['revision']:
                    raise editor.EditError('Arquivo mudou durante a recuperação',409)
                target,_=editor._file(vault,path);target.unlink()
        if action=='concluir':
            put(new,moved['depois'])
            for c in changes:
                if c['path']!=old:
                    put(c['path'],c['depois'])
            remove(old)
        else:
            put(old,moved['antes'])
            for c in changes:
                if c['path']!=old:
                    put(c['path'],c['antes'])
            remove(new)
        data['estado']='concluido' if action=='concluir' else 'revertido'
        editor._atomic(file,json.dumps(data,ensure_ascii=False).encode(),mode=0o600)
    return {**report,'estado':data['estado'],'indice':index_vault(vault,quiet=True,esperar=5)}
