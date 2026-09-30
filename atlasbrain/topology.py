"""Grafo dirigido de arquivos e símbolos, com evidência e consultas limitadas.

A AST não resolve tipos em runtime. Ligações sem binding explícito são INFERRED;
nomes com mais de um destino permanecem ambíguos e não entram como dependências.
"""

from .localization import text as _text, value as _value

import json
import re
from collections import defaultdict, deque

import networkx as nx

from .code import resolve_modules
from .db import get_meta
from .graph import build_relations

RELATIONS = {'manual', 'importa', 'chama', 'herda', 'contem', 'menciona', 'wikilink', 'mdlink', 'similar'}
CODE = {'importa', 'chama', 'herda'}


def build(con, vault):
    G = build_relations(con, similar=True)
    paths = {n: d['path'] for n, d in G.nodes(data=True)}
    mod_to_file = resolve_modules(paths, vault)
    by_name, by_qualified = defaultdict(list), defaultdict(list)
    for s in con.execute('SELECT note_id, nome, tipo, linha, pai FROM simbolos ORDER BY note_id, linha'):
        qualified = f"{s['pai']}.{s['nome']}" if s['pai'] else s['nome']
        node = f"{paths[s['note_id']]}::{qualified}@{s['linha']}"
        G.add_node(node, path=paths[s['note_id']], label=qualified, nome=s['nome'], kind='simbolo',
                   simbolo_tipo=s['tipo'], linha=s['linha'], arquivo=s['note_id'], pai=s['pai'])
        by_name[s['nome']].append(node)
        by_qualified[s['note_id'], qualified].append(node)
        G.add_edge(s['note_id'], node, kind='contem', conf='EXTRACTED', linha=s['linha'])
    for n, d in list(G.nodes(data=True)):
        if d['kind'] == 'simbolo' and d['pai']:
            parents = by_qualified.get((d['arquivo'], d['pai']), [])
            if len(parents) == 1:
                G.add_edge(parents[0], n, kind='contem', conf='EXTRACTED', linha=d['linha'])
    refs = con.execute('SELECT note_id, tipo, alvo, linha, quem FROM refs ORDER BY note_id, linha').fetchall()
    bindings = defaultdict(list)
    exports = defaultdict(list)
    shadows = defaultdict(list)
    imported = defaultdict(set)
    for r in refs:
        if r['tipo'] == 'binding':
            module, original = json.loads(r['alvo'])
            qualified = r['quem']
            scope, local = qualified.split('::', 1) if '::' in qualified else ('', qualified)
            bindings[r['note_id'], scope, local].append((mod_to_file(r['note_id'], module), original))
        elif r['tipo'] == 'export':
            exports[r['note_id'], r['quem']].append(r['alvo'])
        elif r['tipo'] == 'shadow':
            shadows[r['note_id'], r['quem'] or '', r['alvo']].append(r['linha'])
        elif r['tipo'] == 'import':
            dst = mod_to_file(r['note_id'], r['alvo'])
            if dst is not None:
                imported[r['note_id']].add(dst)
                for edge in G.get_edge_data(r['note_id'], dst, default={}).values():
                    if edge['kind'] == 'importa' and edge.get('linha') is None:
                        edge['linha'] = r['linha']
    ambiguous = []

    def target_in_file(file, name):
        if file is None:
            return []
        return by_qualified.get((file, name), [])

    for r in refs:
        if r['tipo'] not in ('call', 'inherits'):
            continue
        file, owner, target = r['note_id'], r['quem'], r['alvo']
        sources = by_qualified.get((file, owner), []) if owner else [file]
        # Uma declaração repetida/overload não fornece um chamador inequívoco.
        sources = [s for s in sources if not isinstance(s, str) or G.nodes[s]['linha'] <= r['linha']]
        if len(sources) != 1:
            continue
        src = sources[0]
        recv, name = target.rsplit('.', 1) if '.' in target else ('', target)
        cand, conf, explicit = [], 'EXTRACTED', False
        scope = owner or ''
        local = []
        shadow = None
        while True:
            # Nomes locais vencem imports. self/this são resolvidos pela classe, não como parâmetros.
            class_scope = any(G.nodes[n]['simbolo_tipo'] == 'classe'
                              for n in by_qualified.get((file, scope), []))
            lexical_class = class_scope and paths[file].endswith(('.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx'))
            if not recv and not lexical_class:
                local = target_in_file(file, scope + '.' + name if scope else name)
            if recv not in ('self', 'this', 'cls') and shadows.get((file, scope, recv or name)):
                shadow = scope
                break
            if local or bindings.get((file, scope, recv or name)) or not scope:
                break
            scope = scope.rpartition('.')[0]
        if shadow is not None:
            # Arrow/lambda declarada nesse escopo continua sendo um símbolo chamável.
            qname = (shadow + '.' if shadow else '') + (recv or name)
            local = [n for n in local if G.nodes[n]['label'] == qname and
                     G.nodes[n]['linha'] in shadows[file, shadow, recv or name]]
            if not local:
                continue
        if recv in ('self', 'this', 'cls'):
            scope = owner or ''
            while scope:
                classes = [n for n in by_qualified.get((file, scope), []) if G.nodes[n]['simbolo_tipo'] == 'classe']
                if classes:
                    cand = target_in_file(file, scope + '.' + name)
                    break
                scope = scope.rpartition('.')[0]
            explicit = True
        elif local:
            cand = local
            explicit = True
        elif bindings.get((file, scope, recv or name)):
            explicit = True
            for dst, original in bindings[file, scope, recv or name]:
                if original == 'default':
                    names = exports.get((dst, 'default'), [])
                    if len(names) != 1:
                        continue
                    original = names[0]
                if recv:
                    qualified = name if original is None else original + '.' + name
                elif original is None:
                    # Um módulo não é uma função: não inferir chamada para símbolo homônimo.
                    continue
                else:
                    qualified = original
                cand.extend(target_in_file(dst, qualified))
        elif recv:
            cand = target_in_file(file, recv + '.' + name)
            explicit = bool(cand)
        if not cand and not explicit:
            # Fallback conservador: import do arquivo ou nome global único, sem afirmar binding.
            candidates = [n for n in by_name.get(name, []) if G.nodes[n]['arquivo'] != file]
            via_import = [n for n in candidates if G.nodes[n]['arquivo'] in imported[file]]
            cand = via_import or candidates
            if recv:
                cand = [n for n in cand if G.nodes[n]['pai'] and
                        recv.lower() == G.nodes[n]['pai'].split('.')[-1].lower()]
            conf = 'INFERRED'
        cand = list(dict.fromkeys(cand))
        if len(cand) > 1:
            ambiguous.append(dict(path=paths[file], linha=r['linha'], alvo=target, candidatos=cand))
        elif len(cand) == 1:
            G.add_edge(src, cand[0], kind='chama' if r['tipo'] == 'call' else 'herda',
                       conf=conf, linha=r['linha'], detalhe=target)
    G.graph['ambiguas'] = ambiguous
    return G


def snapshot(ctx):
    """Cache por revisão e conexão: uma mudança confirmada no SQLite invalida a topologia."""
    con = ctx.con
    with ctx.db_lock:
        con.execute('SAVEPOINT topologia')
        try:
            key = (id(con), get_meta(con, 'rev'), con.total_changes,
                   con.execute('PRAGMA data_version').fetchone()[0])
            if getattr(ctx, '_topology_key', None) != key:
                ctx._topology = build(con, ctx.vault)
                ctx._topology_key = key
            return ctx._topology
        finally:
            con.execute('RELEASE topologia')


def identify(G, target):
    """Caminho exato, ID de símbolo, arquivo::Classe.metodo, arquivo:linha ou nome único."""
    if target in G:
        return [target]
    if re.fullmatch(r'#\d+', target) and int(target[1:]) in G:
        return [int(target[1:])]
    exact = [n for n, d in G.nodes(data=True) if d['path'] == target and d['kind'] != 'simbolo']
    if exact:
        return exact
    symbol = [n for n, d in G.nodes(data=True) if d['kind'] == 'simbolo' and
              (target == f"{d['path']}::{d['label']}" or target == f"{d['path']}:{d['linha']}")]
    if symbol:
        return symbol
    symbol = [n for n, d in G.nodes(data=True) if d['kind'] == 'simbolo' and
              target.casefold() in (d['label'].casefold(), d['nome'].casefold())]
    if symbol:
        return symbol
    return [n for n, d in G.nodes(data=True) if d['kind'] != 'simbolo' and d['label'].casefold() == target.casefold()]


def reference(G, n):
    d = G.nodes[n]
    if d['kind'] == 'simbolo':
        return f"{d['path']}:{d['linha']} `{d['label']}` [id: {n}]"
    return f"{d['path']}"


def choose(G, target):
    nodes = identify(G, target)
    if len(nodes) == 1:
        return nodes[0], None
    if not nodes:
        return None, f"{_text('Não achei `')}{target}{_text('` no grafo. Use `arquivos`, `onde` ou `buscar`.')}"
    return None, f"`{target}{_text('` é ambíguo; informe o caminho ou ID:\n')}" + '\n'.join(
        '- ' + reference(G, n) for n in nodes[:20]) + (f"\n… {len(nodes) - 20}{_text(' opções adicionais.')}" if len(nodes) > 20 else '')


def validate(depth, tokens, direction, mode='bfs', relations=None):
    if not 0 <= depth <= 6:
        return _text('profundidade deve estar entre 0 e 6.')
    if not 128 <= tokens <= 16000:
        return _text('tokens deve estar entre 128 e 16000.')
    if direction not in ('entrada', 'saida', 'ambas'):
        return _text('direcao deve ser entrada, saida ou ambas.')
    if mode not in ('bfs', 'dfs'):
        return _text('modo deve ser bfs ou dfs.')
    if relations is not None and set(relations) - RELATIONS:
        return _text('Relações válidas: ') + ', '.join(sorted(RELATIONS)) + '.'
    return None


def steps(G, node, direction, relations, inferred=True):
    if direction in ('saida', 'ambas'):
        for a, b, key, d in G.out_edges(node, keys=True, data=True):
            if d['kind'] in relations and (inferred or d['conf'] == 'EXTRACTED'):
                yield b, (a, b, key), d
    if direction in ('entrada', 'ambas'):
        for a, b, key, d in G.in_edges(node, keys=True, data=True):
            if d['kind'] in relations and (inferred or d['conf'] == 'EXTRACTED'):
                yield a, (a, b, key), d


def traverse(G, seeds, depth, direction, relations, mode='bfs', inferred=True, limit=300):
    frontier = deque((n, 0) for n in seeds)
    depths = {n: 0 for n in seeds}
    edges, expanded = {}, {}
    order = {}
    cut = False
    while frontier:
        n, current_depth = frontier.popleft() if mode == 'bfs' else frontier.pop()
        order.setdefault(n, None)
        if current_depth >= depth or expanded.get(n, current_depth + 1) <= current_depth:
            continue
        expanded[n] = current_depth
        neighbors = list(steps(G, n, direction, relations, inferred))
        # DFS usa ordem estável; caminhos mais curtos encontrados depois podem reexpandir o nó.
        for dst, edge, attrs in (reversed(neighbors) if mode == 'dfs' else neighbors):
            if len(edges) >= 1200 or (dst not in depths and len(depths) >= limit):
                cut = True
                continue
            edges[edge] = attrs
            new_depth = current_depth + 1
            if dst not in depths or new_depth < depths[dst]:
                depths[dst] = new_depth
                frontier.append((dst, new_depth))
    return {n: depths[n] for n in order}, edges, cut


def limit_output(lines, tokens, truncated=False):
    # Estimativa explícita; UTF-8/4 evita que acentos escondam respostas grandes.
    budget = tokens * 4
    warning = _text('[TRUNCADO: limite de contexto atingido; reduza a consulta ou aumente tokens/profundidade.]')
    reserve = len(warning.encode()) + 1
    out, size = [], 0
    for line in (part for line in lines for part in line.splitlines()):
        cost = len(line.encode()) + 1
        if size + cost > budget - reserve:
            truncated = True
            break
        out.append(line)
        size += cost
    if truncated:
        out.append(warning)
    return '\n'.join(out)


def query(ctx, query, mode='bfs', depth=2, tokens=2000, direction='ambas', relations=None):
    error = validate(depth, tokens, direction, mode, relations)
    if error:
        return error
    G = snapshot(ctx)
    seeds = identify(G, query)
    if len(seeds) > 1:
        return limit_output([choose(G, query)[1]], tokens)
    if not seeds:
        with ctx.db_lock:
            hits = ctx.searcher.search(query, limit=3)
        paths = {r['path'] for r in hits}
        seeds = [n for n, d in G.nodes(data=True) if d['kind'] != 'simbolo' and d['path'] in paths]
    if not seeds:
        return f"{_text('Nenhum resultado para `')}{query}`."
    kinds = RELATIONS - {'similar'} if relations is None else set(relations)
    depths, edges, cut = traverse(G, seeds, depth, direction, kinds, mode)
    lines = [f"{_text('Grafo: ')}{mode.upper()}{_text(' · direção ')}{_value(direction)}{_text(' · até ')}{depth}{_text(' salto(s).')}",
             _text('Setas seguem a relação original. INFERRED é hipótese. Tokens estimados por UTF-8/4.')]
    # Intercalar nós e evidências permite que mesmo uma resposta curta preserve relações.
    emitted, shown_edges = set(), set()
    for n in depths:
        if n not in emitted:
            lines.append(_text('NÓ ') + reference(G, n))
            emitted.add(n)
        for edge, attrs in edges.items():
            if n not in edge[:2] or edge in shown_edges:
                continue
            a, b, _ = edge
            lines.append(f"{reference(G, a)} → {reference(G, b)} · {_value(attrs['kind'])} · {attrs['conf']}" +
                         (f"{_text(' · evidência L')}{attrs['linha']}" if attrs.get('linha') else ''))
            emitted.update((a, b))
            shown_edges.add(edge)
    ambiguous = [a for a in G.graph['ambiguas'] if any(G.nodes[n]['path'] == a['path'] for n in depths)]
    if ambiguous:
        lines.append(f"{len(ambiguous)}{_text(' referência(s) ambígua(s) nesses arquivos ficaram sem aresta:')}")
        lines.extend(f"- {a['path']}:{a['linha']} `{a['alvo']}`" for a in ambiguous[:10])
    return limit_output(lines, tokens, cut)


def impact(ctx, target, depth=3, tokens=2000, include_inferred=False):
    error = validate(depth, tokens, 'entrada')
    if error:
        return error
    G = snapshot(ctx)
    node, error = choose(G, target)
    if error:
        return limit_output([error], tokens)
    seeds = [node]
    contained_cut = False
    if G.nodes[node]['kind'] != 'simbolo':
        seeds += [n for n in G.successors(node) if G.nodes[n]['kind'] == 'simbolo']
    elif G.nodes[node]['simbolo_tipo'] == 'classe':
        contained, _, contained_cut = traverse(G, seeds, 6, 'saida', {'contem'})
        seeds = list(contained)
    cut = contained_cut or len(seeds) > 300
    depths, edges, overflow = traverse(G, seeds[:300], depth, 'entrada', CODE,
                                       inferred=include_inferred)
    impacted = [n for n in depths if n not in seeds]
    lines = [f"{_text('Impacto potencial de ')}{reference(G, node)}{_text(' · até ')}{depth}{_text(' salto(s).')}",
             _text('Dependências estáticas; não é garantia de quebra nem de cobertura completa.'),
             _text('INFERRED incluídas.') if include_inferred else _text('Somente relações EXTRACTED; INFERRED ficam de fora.')]
    if not impacted:
        lines.append(_text('Nenhum dependente encontrado dentro desses limites.'))
    for n in sorted(impacted, key=lambda x: depths[x]):
        lines.append(f"- {depths[n]}{_text(' salto(s): ')}{reference(G, n)}")
        for (a, b, _), d in edges.items():
            if a == n:
                lines.append(f"  {reference(G, a)} → {reference(G, b)} · {_value(d['kind'])} · {d['conf']}" +
                             (f"{_text(' · evidência L')}{d['linha']}" if d.get('linha') else ''))
    relevant_paths = {G.nodes[n]['path'] for n in depths}
    ambiguous = [a for a in G.graph['ambiguas'] if a['path'] in relevant_paths or
                 any(c in depths for c in a['candidatos'])]
    if ambiguous:
        lines.append(f"{len(ambiguous)}{_text(' referência(s) ambígua(s) podem ocultar dependentes:')}")
        lines.extend(f"- {a['path']}:{a['linha']} `{a['alvo']}`" for a in ambiguous[:10])
    return limit_output(lines, tokens, cut or overflow)
