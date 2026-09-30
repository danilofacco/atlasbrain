"""Grafo dirigido de arquivos e símbolos, com evidência e consultas limitadas.

A AST não resolve tipos em runtime. Ligações sem binding explícito são INFERRED;
nomes com mais de um destino permanecem ambíguos e não entram como dependências.
"""

from .localization import text as _text, value as _value

import json
import re
from collections import defaultdict, deque

import networkx as nx

from .codigo import resolver_modulos
from .db import get_meta
from .graph import build_relations

RELACOES = {'manual', 'importa', 'chama', 'herda', 'contem', 'menciona', 'wikilink', 'mdlink', 'similar'}
CODIGO = {'importa', 'chama', 'herda'}


def construir(con, vault):
    G = build_relations(con, similar=True)
    paths = {n: d['path'] for n, d in G.nodes(data=True)}
    mod_to_file = resolver_modulos(paths, vault)
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
            if getattr(ctx, '_topologia_key', None) != key:
                ctx._topologia = construir(con, ctx.vault)
                ctx._topologia_key = key
            return ctx._topologia
        finally:
            con.execute('RELEASE topologia')


def identificar(G, alvo):
    """Caminho exato, ID de símbolo, arquivo::Classe.metodo, arquivo:linha ou nome único."""
    if alvo in G:
        return [alvo]
    if re.fullmatch(r'#\d+', alvo) and int(alvo[1:]) in G:
        return [int(alvo[1:])]
    exact = [n for n, d in G.nodes(data=True) if d['path'] == alvo and d['kind'] != 'simbolo']
    if exact:
        return exact
    symbol = [n for n, d in G.nodes(data=True) if d['kind'] == 'simbolo' and
              (alvo == f"{d['path']}::{d['label']}" or alvo == f"{d['path']}:{d['linha']}")]
    if symbol:
        return symbol
    symbol = [n for n, d in G.nodes(data=True) if d['kind'] == 'simbolo' and
              alvo.casefold() in (d['label'].casefold(), d['nome'].casefold())]
    if symbol:
        return symbol
    return [n for n, d in G.nodes(data=True) if d['kind'] != 'simbolo' and d['label'].casefold() == alvo.casefold()]


def referencia(G, n):
    d = G.nodes[n]
    if d['kind'] == 'simbolo':
        return f"{d['path']}:{d['linha']} `{d['label']}` [id: {n}]"
    return f"{d['path']}"


def escolher(G, alvo):
    nodes = identificar(G, alvo)
    if len(nodes) == 1:
        return nodes[0], None
    if not nodes:
        return None, f"{_text('Não achei `')}{alvo}{_text('` no grafo. Use `arquivos`, `onde` ou `buscar`.')}"
    return None, f"`{alvo}{_text('` é ambíguo; informe o caminho ou ID:\n')}" + '\n'.join(
        '- ' + referencia(G, n) for n in nodes[:20]) + (f"\n… {len(nodes) - 20}{_text(' opções adicionais.')}" if len(nodes) > 20 else '')


def validar(profundidade, tokens, direcao, modo='bfs', relacoes=None):
    if not 0 <= profundidade <= 6:
        return _text('profundidade deve estar entre 0 e 6.')
    if not 128 <= tokens <= 16000:
        return _text('tokens deve estar entre 128 e 16000.')
    if direcao not in ('entrada', 'saida', 'ambas'):
        return _text('direcao deve ser entrada, saida ou ambas.')
    if modo not in ('bfs', 'dfs'):
        return _text('modo deve ser bfs ou dfs.')
    if relacoes is not None and set(relacoes) - RELACOES:
        return _text('Relações válidas: ') + ', '.join(sorted(RELACOES)) + '.'
    return None


def passos(G, node, direcao, relacoes, inferidas=True):
    if direcao in ('saida', 'ambas'):
        for a, b, key, d in G.out_edges(node, keys=True, data=True):
            if d['kind'] in relacoes and (inferidas or d['conf'] == 'EXTRACTED'):
                yield b, (a, b, key), d
    if direcao in ('entrada', 'ambas'):
        for a, b, key, d in G.in_edges(node, keys=True, data=True):
            if d['kind'] in relacoes and (inferidas or d['conf'] == 'EXTRACTED'):
                yield a, (a, b, key), d


def percorrer(G, seeds, profundidade, direcao, relacoes, modo='bfs', inferidas=True, limite=300):
    frontier = deque((n, 0) for n in seeds)
    depths = {n: 0 for n in seeds}
    edges, expanded = {}, {}
    order = {}
    cut = False
    while frontier:
        n, depth = frontier.popleft() if modo == 'bfs' else frontier.pop()
        order.setdefault(n, None)
        if depth >= profundidade or expanded.get(n, profundidade + 1) <= depth:
            continue
        expanded[n] = depth
        neighbors = list(passos(G, n, direcao, relacoes, inferidas))
        # DFS usa ordem estável; caminhos mais curtos encontrados depois podem reexpandir o nó.
        for dst, edge, attrs in (reversed(neighbors) if modo == 'dfs' else neighbors):
            if len(edges) >= 1200 or (dst not in depths and len(depths) >= limite):
                cut = True
                continue
            edges[edge] = attrs
            new_depth = depth + 1
            if dst not in depths or new_depth < depths[dst]:
                depths[dst] = new_depth
                frontier.append((dst, new_depth))
    return {n: depths[n] for n in order}, edges, cut


def limitar(lines, tokens, truncado=False):
    # Estimativa explícita; UTF-8/4 evita que acentos escondam respostas grandes.
    budget = tokens * 4
    aviso = _text('[TRUNCADO: limite de contexto atingido; reduza a consulta ou aumente tokens/profundidade.]')
    reserve = len(aviso.encode()) + 1
    out, size = [], 0
    for line in (part for line in lines for part in line.splitlines()):
        cost = len(line.encode()) + 1
        if size + cost > budget - reserve:
            truncado = True
            break
        out.append(line)
        size += cost
    if truncado:
        out.append(aviso)
    return '\n'.join(out)


def consultar(ctx, consulta, modo='bfs', profundidade=2, tokens=2000, direcao='ambas', relacoes=None):
    error = validar(profundidade, tokens, direcao, modo, relacoes)
    if error:
        return error
    G = snapshot(ctx)
    seeds = identificar(G, consulta)
    if len(seeds) > 1:
        return limitar([escolher(G, consulta)[1]], tokens)
    if not seeds:
        with ctx.db_lock:
            hits = ctx.searcher.search(consulta, limit=3)
        paths = {r['path'] for r in hits}
        seeds = [n for n, d in G.nodes(data=True) if d['kind'] != 'simbolo' and d['path'] in paths]
    if not seeds:
        return f"{_text('Nenhum resultado para `')}{consulta}`."
    kinds = RELACOES - {'similar'} if relacoes is None else set(relacoes)
    depths, edges, cut = percorrer(G, seeds, profundidade, direcao, kinds, modo)
    lines = [f"{_text('Grafo: ')}{modo.upper()}{_text(' · direção ')}{_value(direcao)}{_text(' · até ')}{profundidade}{_text(' salto(s).')}",
             _text('Setas seguem a relação original. INFERRED é hipótese. Tokens estimados por UTF-8/4.')]
    # Intercalar nós e evidências permite que mesmo uma resposta curta preserve relações.
    emitted, shown_edges = set(), set()
    for n in depths:
        if n not in emitted:
            lines.append(_text('NÓ ') + referencia(G, n))
            emitted.add(n)
        for edge, attrs in edges.items():
            if n not in edge[:2] or edge in shown_edges:
                continue
            a, b, _ = edge
            lines.append(f"{referencia(G, a)} → {referencia(G, b)} · {_value(attrs['kind'])} · {attrs['conf']}" +
                         (f"{_text(' · evidência L')}{attrs['linha']}" if attrs.get('linha') else ''))
            emitted.update((a, b))
            shown_edges.add(edge)
    ambiguous = [a for a in G.graph['ambiguas'] if any(G.nodes[n]['path'] == a['path'] for n in depths)]
    if ambiguous:
        lines.append(f"{len(ambiguous)}{_text(' referência(s) ambígua(s) nesses arquivos ficaram sem aresta:')}")
        lines.extend(f"- {a['path']}:{a['linha']} `{a['alvo']}`" for a in ambiguous[:10])
    return limitar(lines, tokens, cut)


def impacto(ctx, alvo, profundidade=3, tokens=2000, incluir_inferidas=False):
    error = validar(profundidade, tokens, 'entrada')
    if error:
        return error
    G = snapshot(ctx)
    node, error = escolher(G, alvo)
    if error:
        return limitar([error], tokens)
    seeds = [node]
    contained_cut = False
    if G.nodes[node]['kind'] != 'simbolo':
        seeds += [n for n in G.successors(node) if G.nodes[n]['kind'] == 'simbolo']
    elif G.nodes[node]['simbolo_tipo'] == 'classe':
        contained, _, contained_cut = percorrer(G, seeds, 6, 'saida', {'contem'})
        seeds = list(contained)
    cut = contained_cut or len(seeds) > 300
    depths, edges, overflow = percorrer(G, seeds[:300], profundidade, 'entrada', CODIGO,
                                       inferidas=incluir_inferidas)
    impacted = [n for n in depths if n not in seeds]
    lines = [f"{_text('Impacto potencial de ')}{referencia(G, node)}{_text(' · até ')}{profundidade}{_text(' salto(s).')}",
             _text('Dependências estáticas; não é garantia de quebra nem de cobertura completa.'),
             _text('INFERRED incluídas.') if incluir_inferidas else _text('Somente relações EXTRACTED; INFERRED ficam de fora.')]
    if not impacted:
        lines.append(_text('Nenhum dependente encontrado dentro desses limites.'))
    for n in sorted(impacted, key=lambda x: depths[x]):
        lines.append(f"- {depths[n]}{_text(' salto(s): ')}{referencia(G, n)}")
        for (a, b, _), d in edges.items():
            if a == n:
                lines.append(f"  {referencia(G, a)} → {referencia(G, b)} · {_value(d['kind'])} · {d['conf']}" +
                             (f"{_text(' · evidência L')}{d['linha']}" if d.get('linha') else ''))
    relevant_paths = {G.nodes[n]['path'] for n in depths}
    ambiguous = [a for a in G.graph['ambiguas'] if a['path'] in relevant_paths or
                 any(c in depths for c in a['candidatos'])]
    if ambiguous:
        lines.append(f"{len(ambiguous)}{_text(' referência(s) ambígua(s) podem ocultar dependentes:')}")
        lines.extend(f"- {a['path']}:{a['linha']} `{a['alvo']}`" for a in ambiguous[:10])
    return limitar(lines, tokens, cut or overflow)
