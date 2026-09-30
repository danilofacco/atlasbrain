"""Shared, evidence-based reports for the browser and MCP."""

from .localization import text as _text
import difflib
import hashlib
import json
import time
from pathlib import PurePosixPath

from .db import get_meta, set_meta


def capture(con):
    current = {
        'arquivos': {r['path']: r['hash'] for r in con.execute('SELECT path,hash FROM notes')},
        'ligacoes': sorted([list(e) for e in {(*(sorted((r[0],r[1])) if r[2]=='similar' else (r[0],r[1])), r[2], r[3] or '') for r in con.execute(
            'SELECT a.path,b.path,l.kind,l.conf FROM links l JOIN notes a ON a.id=l.src JOIN notes b ON b.id=l.dst')}])}
    before = json.loads(get_meta(con, 'snapshot_atual', '{}'))
    # Keep the latest meaningful comparison across automatic no-change scans.
    if current == {k: before.get(k) for k in current}:
        return
    if before:
        set_meta(con, 'snapshot_anterior', json.dumps(before, ensure_ascii=False))
    current['data'] = time.time()
    set_meta(con, 'snapshot_atual', json.dumps(current, ensure_ascii=False))


def changes(con, offset=0, limit=30):
    old = json.loads(get_meta(con, 'snapshot_anterior', '{}'))
    new = json.loads(get_meta(con, 'snapshot_atual', '{}'))
    items = []
    if old and new:
        a, b = old['arquivos'], new['arquivos']
        for path in sorted(a.keys() | b.keys()):
            action = 'adicionado' if path not in a else 'removido' if path not in b else 'alterado' if a[path] != b[path] else None
            if action:
                items.append(dict(tipo='arquivo', acao=action, path=path))
        a, b = {tuple(e) for e in old['ligacoes']}, {tuple(e) for e in new['ligacoes']}
        for action, edges in [('adicionada', b-a), ('removida', a-b)]:
            items.extend(dict(tipo='ligacao', acao=action, de=e[0], para=e[1], relacao=e[2], origem=e[3]) for e in sorted(edges))
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    return dict(base=old.get('data'), atual=new.get('data'), disponivel=bool(old and new), total=len(items),
                itens=items[offset:offset+limit], offset=offset, mais=offset+limit<len(items))


def status(con, vault, limit=30, offset=0):
    from .indexer import scan
    indexed = {r['path']: r for r in con.execute('SELECT path,mtime,size FROM notes')}
    skipped = json.loads(get_meta(con, 'pulados', '{}'))
    files = scan(vault)
    import fcntl
    from .config import data_dir
    indexing = False
    lock_file = data_dir(vault) / 'index.lock'
    try:
        with lock_file.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            except BlockingIOError:
                indexing = True
    except OSError:
        pass
    pending = []
    for path, file in sorted(files.items()):
        try:
            st = file.stat()
        except OSError:
            pending.append(dict(path=path, estado='inacessivel'))
            continue
        r = indexed.get(path)
        if not r and path in skipped and skipped[path][1:] == [st.st_mtime, st.st_size]:
            continue
        if not r or st.st_mtime != r['mtime'] or st.st_size != r['size']:
            pending.append(dict(path=path, estado='novo' if not r else 'alterado'))
    pending.extend(dict(path=p, estado='removido') for p in sorted(indexed.keys()-files.keys()))
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    return dict(indexado=float(get_meta(con, 'last_indexed', '0')), revisao=int(get_meta(con, 'rev', '0')),
                arquivos=len(indexed), indexando=indexing, pendentes=len(pending), itens=pending[offset:offset+limit],
                offset=offset, mais=offset+limit<len(pending), atualizado=not pending and not indexing,
                criterio=_text('Compara tamanho e data dos arquivos indexáveis com o índice; não reindexa nem prova ausência de mudanças com metadados idênticos.'))


def suggestions(con, path, limit=5):
    row = con.execute('SELECT path,title FROM notes WHERE path=?', (path,)).fetchone()
    if not row:
        raise ValueError(_text('Arquivo não encontrado no índice'))
    linked = {r[0] for r in con.execute("SELECT b.path FROM links l JOIN notes b ON b.id=l.dst JOIN notes a ON a.id=l.src WHERE a.path=? AND l.kind!='similar' UNION SELECT a.path FROM links l JOIN notes a ON a.id=l.src JOIN notes b ON b.id=l.dst WHERE b.path=? AND l.kind!='similar'", (path, path))}
    result = []
    for r in con.execute('SELECT path,title FROM notes WHERE path!=? ORDER BY path', (path,)):
        if r['path'] in linked:
            continue
        same_folder = PurePosixPath(path).parent == PurePosixPath(r['path']).parent
        similarity = difflib.SequenceMatcher(None, (row['title'] or '').lower(), (r['title'] or '').lower()).ratio()
        if not same_folder and similarity < .6:
            continue
        score = round(min(.85, .35*same_folder + .5*similarity), 3)
        evidence = []
        if same_folder:
            evidence.append(_text('Arquivos na mesma pasta'))
        if similarity >= .6:
            evidence.append(_text('Nomes semelhantes'))
        result.append(dict(path=r['path'], title=r['title'], confianca=score, origem='INFERRED', evidencias=evidence,
                           aviso=_text('Proximidade de nomes/pastas não prova dependência. Revise ambos antes de registrar.')))
    result.sort(key=lambda r: (-r['confianca'], r['path']))
    return dict(path=path, sugestoes=result[:max(1, min(int(limit), 20))])


def register_link(con, vault, source, target, reason):
    if source == target or not reason.strip() or len(reason) > 2000:
        raise ValueError(_text('Escolha dois arquivos distintos e um motivo de até 2000 caracteres'))
    for path in [source, target]:
        if not con.execute('SELECT 1 FROM notes WHERE path=?', (path,)).fetchone():
            raise ValueError(_text('Arquivo não encontrado no índice'))
    import yaml
    from .config import notes_dir
    key = hashlib.sha256((source+'\0'+target).encode()).hexdigest()[:20]
    folder = notes_dir(vault) / 'Vinculos'
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / (key+'.md')
    content = '---\n'+yaml.safe_dump(dict(tipo='vinculo', origem_arquivo=source, destino_arquivo=target, motivo=reason.strip()), allow_unicode=True)+'---\n# Vínculo revisado\n\n'+reason.strip()+'\n'
    from . import editor
    with editor._lock(vault):
        rel = file.relative_to(vault).as_posix()
        current = editor.read(vault, rel) if file.exists() else None
        editor.save(vault, rel, content, current['revision'] if current else None, create=current is None)
    return dict(path=file.relative_to(vault).as_posix(), de=source, para=target, origem='DECLARED')


def sync_links(con):
    con.execute("DELETE FROM links WHERE kind='manual'")
    ids = {r['path']: r['id'] for r in con.execute('SELECT id,path FROM notes')}
    for r in con.execute('SELECT frontmatter FROM notes'):
        fm = json.loads(r[0] or '{}')
        a, b = fm.get('origem_arquivo'), fm.get('destino_arquivo')
        if fm.get('tipo') == 'vinculo' and a in ids and b in ids and a != b:
            con.execute("INSERT INTO links(src,target,dst,kind,conf,detalhe) VALUES(?,?,?,'manual','DECLARED',?)", (ids[a], b, ids[b], str(fm.get('motivo', ''))))


def budget_report(data, tokens=2000):
    """Conservative size ceiling, including metadata. Does not claim tokenizer accuracy."""
    limit = max(256, min(int(tokens), 12000))*3
    original_items = len(data.get('itens', []))
    from copy import deepcopy
    data = deepcopy(data)
    data['truncado'] = False
    omitted = {}
    if 'itens' in data: data['retornados'] = original_items
    def encode():
        return json.dumps(data, ensure_ascii=False)
    for key in ('itens', 'dependencias', 'pendencias', 'arquivos', 'decisoes', 'decisoes_historicas', 'sugestoes'):
        while isinstance(data.get(key), list) and data[key] and len(encode()) > limit:
            data[key].pop()
            omitted[key] = omitted.get(key, 0) + 1
            data['omitidos_por_orcamento'] = omitted
            data['truncado'] = True
    if 'offset' in data and isinstance(data.get('itens'), list):
        data['retornados'] = len(data['itens'])
        data['proximo_offset'] = data['offset'] + len(data['itens'])
        if data['truncado']: data['mais'] = True
    if len(encode()) > limit:
        return json.dumps(dict(truncado=True, omitidos_por_orcamento=omitted, aviso=_text('Orçamento insuficiente; aumente tokens ou use uma ferramenta específica.')), ensure_ascii=False)
    return encode()


def task_context(ctx, task, limit=8, focus=None, objective='implementar'):
    if not task.strip():
        raise ValueError(_text('Descreva a tarefa'))
    if objective not in ('implementar','investigar','revisar','documentar'):
        raise ValueError(_text('Objetivo inválido'))
    from . import graph
    target_limit = max(1, min(limit, 20))
    hits = ctx.searcher.search(task[:2000], limit=max(20, min(50, target_limit * 4)))
    def active(row):
        fm = json.loads(row['frontmatter'] or '{}')
        return fm.get('tipo') != 'decisao' or fm.get('status', 'ativa') == 'ativa'
    # Históricas podem ser encontradas pela busca geral, mas não ocupam vagas no contexto vigente.
    hits = [h for h in hits if (row := ctx.con.execute(
        'SELECT frontmatter FROM notes WHERE path=?', (h['path'],)).fetchone()) and active(row)]
    paths = list(dict.fromkeys(h['path'] for h in hits))
    files, dependencies, decisions, pending = [], [], [], []
    risks = []
    focus_paths = []
    historical_focus = []
    for ref in (focus or [])[:5]:
        row = graph.find_note(ctx.con, ref)
        if row and not active(row):
            fm = json.loads(row['frontmatter'] or '{}')
            successor = fm.get('substituida_por_arquivo')
            if not successor and fm.get('substituida_por'):
                next_row = graph.find_note(ctx.con, str(fm['substituida_por']).strip('[]'))
                successor = next_row['path'] if next_row else None
            historical_focus.append(dict(path=row['path'], status=fm.get('status'), substituta=successor))
            risks.append(_text('Foco em decisão histórica: ')+row['path'])
            next_row = ctx.con.execute('SELECT path,frontmatter FROM notes WHERE path=?', (successor,)).fetchone() if successor else None
            if next_row and active(next_row) and next_row['path'] not in focus_paths:
                focus_paths.append(next_row['path'])
        elif row and row['path'] not in focus_paths:
            focus_paths.append(row['path'])
        elif not row:
            risks.append(_text('Foco não encontrado: ')+ref[:160])
    paths = list(dict.fromkeys(focus_paths + paths))[:target_limit]
    from .consolidation import coverage
    _, stale = coverage(ctx.con)
    for path in paths:
        r = graph.find_note(ctx.con, path)
        fm = json.loads(r['frontmatter'] or '{}')
        hit = next((h for h in hits if h['path']==path), {})
        entry = dict(path=path, title=r['title'], kind=r['kind'], trecho=hit.get('snippet',r['preview'] or '')[:700],
                     motivo=[_text('foco explícito')] if path in focus_paths else hit.get('motivos', [_text('relevância de busca')]),
                     hash_indice=r['hash'], ler={'nota':path, **({'secao':hit['heading']} if hit.get('heading') else {})})
        if path in stale:
            risks.append(_text('Síntese desatualizada: ')+path)
        if r['kind']=='nota':
            import re
            chunks=ctx.con.execute('SELECT text FROM chunks WHERE note_id=? AND ord>=0 ORDER BY ord LIMIT 20',(r['id'],)).fetchall()
            seen=set()
            for chunk in chunks:
                for pending_task in re.findall(r'^\s*[-*] \[ \] (.+)$', chunk[0], re.M):
                    if pending_task not in seen and len(seen)<5:
                        pending.append({'path':path,'tarefa':pending_task[:300],'origem':_text('checkbox explícito')})
                        seen.add(pending_task)
        if fm.get('tipo') == 'decisao':
            if fm.get('status', 'ativa') == 'ativa':
                decisions.append(entry)
        else:
            files.append(entry)
        nb = graph.neighbors(ctx.con, r['id'])
        for direction, rows in [('saida', nb['links']), ('entrada', nb['backlinks'])]:
            for n in rows[:6]:
                related = ctx.con.execute('SELECT frontmatter FROM notes WHERE path=?', (n['path'],)).fetchone()
                if related and not active(related):
                    continue
                dependencies.append(dict(arquivo=path, path=n['path'], direcao=direction, relacao=n['kind'], origem=n.get('conf'), detalhe=n.get('detalhe')))
    decision_paths = {d['path'] for d in decisions}
    for dependency in dependencies:
        if dependency['path'] in decision_paths:
            continue
        r = graph.find_note(ctx.con, dependency['path'])
        if not r:
            continue
        fm = json.loads(r['frontmatter'] or '{}')
        if fm.get('tipo') == 'decisao' and fm.get('status', 'ativa') == 'ativa':
            decisions.append(dict(path=r['path'], title=r['title'], kind=r['kind'], trecho=(r['preview'] or '')[:400],
                                  relacionada_a=dependency['arquivo'], ler={'nota':r['path']}))
            decision_paths.add(r['path'])
    state = status(ctx.con, ctx.vault, limit=3)
    if state['indexando']:
        risks.append(_text('Indexação em andamento; o contexto pode mudar'))
    if state['pendentes']:
        risks.append(f"{state['pendentes']}{_text(' arquivos pendentes de indexação')}")
    if any(d['origem']=='INFERRED' for d in dependencies):
        risks.append(_text('Há relações inferidas; confirme no código antes de alterar'))
    dependencies.sort(key=lambda d: (d['origem']=='INFERRED',
        d['direcao'] != ('entrada' if objective=='revisar' else 'saida')))
    return dict(tarefa=task[:2000], objetivo=objective, foco=focus_paths, decisoes_historicas=historical_focus,
                pendencias=pending, selecionados={'arquivos':len(files),'decisoes':len(decisions),'dependencias':len(dependencies),'pendencias':len(pending)}, arquivos=files, dependencias=dependencies, decisoes=decisions, riscos=risks,
                indice=state, aviso=_text('Contexto selecionado por relevância de busca; não é uma análise exaustiva. Use impacto/explicar para aprofundar.'))
