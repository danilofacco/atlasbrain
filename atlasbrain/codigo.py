"""Estrutura do código, local e determinística: tree-sitter lê a AST de cada
arquivo e extrai símbolos, imports, chamadas, herança e os comentários de "porquê". Sem LLM.

A interface usa a projeção por arquivos e notas. `topologia` conecta também funções/classes/métodos
das tabelas `simbolos` e `refs`, incluindo chamadas locais. Cada aresta leva procedência: EXTRACTED
(está no código) ou INFERRED (deduzida). Referências AMBIGUOUS ficam sem aresta e são expostas à consulta.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

LANGS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".php": "php", ".swift": "swift", ".kt": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".cxx": "cpp", ".hh": "cpp", ".hxx": "cpp", ".kts": "kotlin", ".bash": "bash",
    ".ex": "elixir", ".exs": "elixir", ".jl": "julia", ".r": "r",
    ".hs": "haskell", ".ml": "ocaml", ".mli": "ocaml", ".pl": "perl", ".pm": "perl",
    ".ps1": "powershell", ".psm1": "powershell",
    ".hpp": "cpp", ".cs": "csharp", ".sh": "bash", ".lua": "lua", ".scala": "scala", ".dart": "dart",
}

DEF_TYPES = {
    "function_definition", "function_declaration", "function_item", "method_definition", "method_declaration",
    "method", "singleton_method", "constructor_declaration", "class_definition", "class_declaration",
    "class", "module", "interface_declaration", "struct_item", "enum_item", "trait_item", "enum_declaration",
    "type_alias_declaration", "protocol_declaration", "object_declaration", "struct_declaration",
    "abstract_class_declaration", "type_spec", "subroutine_declaration_statement", "function_statement",
    "function", "let_binding",
}
CLASSY = ("class", "interface", "struct", "enum", "trait", "protocol", "object", "module", "type_spec", "type_alias")
CALL_TYPES = {"call", "call_expression", "method_invocation", "function_call", "invocation_expression",
              "member_call_expression", "function_call_expression", "application_expression", "apply", "command"}
IMPORT_TYPES = {"import_statement", "import_from_statement", "import_declaration", "use_declaration",
                "preproc_include", "import_spec", "namespace_use_declaration", "using_directive", "use_statement", "import"}
RATIONALE_RE = re.compile(r"\b(NOTE|WHY|HACK|TODO|FIXME|IMPORTANT|DECISION|DECISÃO|PORQUE|POR QUE|ATENÇÃO)\b[:\s-]+(.{6,220})", re.I)


@dataclass
class Estrutura:
    simbolos: list = field(default_factory=list)   # (nome, tipo, linha, pai)
    imports: list = field(default_factory=list)    # (módulo, linha)
    chamadas: list = field(default_factory=list)   # (quem chama, nome chamado, linha)
    herancas: list = field(default_factory=list)   # (classe, base, linha)
    porques: list = field(default_factory=list)    # (etiqueta, texto, linha)
    bindings: list = field(default_factory=list)   # (nome local, módulo, nome original, linha)
    exports: list = field(default_factory=list)    # (export, símbolo, linha)
    sombras: list = field(default_factory=list)    # (escopo, nome, linha)


@lru_cache(maxsize=None)
def _parser(lang: str):
    try:
        from tree_sitter_language_pack import get_parser
        return get_parser(lang)
    except Exception:
        return None


def suportado(ext: str) -> bool:
    return ext.lower() in LANGS


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _name(node, src: bytes) -> str | None:
    n = node.child_by_field_name("name")
    if n is not None:
        return _txt(n, src)
    if node.type == "type_spec":
        return None
    for c in node.children:  # ruby/kotlin/swift às vezes não usam o campo "name"
        if c.type in ("identifier", "constant", "type_identifier", "simple_identifier", "function_name", "variable", "value_name", "bareword"):
            return _txt(c, src)
    signature = next((c for c in node.children if c.type == 'signature'), None)
    if signature is not None:
        call = next((c for c in signature.children if c.type == 'call_expression'), None)
        if call is not None and call.named_children:
            return _txt(call.named_children[0], src)
    pattern = node.child_by_field_name('pattern')
    if pattern is not None and pattern.type == 'value_name':
        return _txt(pattern, src)
    return None


def _callee(node, src: bytes) -> str | None:
    f = node.child_by_field_name("function") or node.child_by_field_name("name") or node.child_by_field_name("method") or node.child_by_field_name("target")
    if f is None and node.children:
        f = node.children[0]
    if f is None:
        return None
    # obj.metodo() / pkg.Func() / a::b() → último identificador, guardando o receptor ("re" em re.search)
    recv = ""
    for field_name in ("attribute", "property", "field", "name"):
        sub = f.child_by_field_name(field_name)
        if sub is not None:
            obj = f.child_by_field_name("object") or f.child_by_field_name("operand") or f.child_by_field_name("value")
            if obj is not None:
                r = re.search(r"([A-Za-z_$][\w$]*)\s*$", _txt(obj, src))
                recv = r.group(1) if r else "?"
            f = sub
            break
    t = _txt(f, src)
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", t)
    if not m:
        return None
    return f"{recv}.{m.group(1)}" if recv else m.group(1)


def _strings(node, src: bytes) -> list[str]:
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in ("string", "string_literal", "interpreted_string_literal", "string_fragment", "system_lib_string"):
            s = _txt(n, src).strip("'\"`<>")
            if s:
                out.append(s)
            continue
        stack.extend(n.children)
    return out


def _import_targets(node, src: bytes) -> list[str]:
    t = node.type
    if t == "import_from_statement":  # python: from .db import connect
        mod = node.child_by_field_name("module_name")
        if mod is None:
            return []
        m = _txt(mod, src)
        if m.strip(".") == "":  # from . import embed → .embed
            return [m + _txt(n, src).split(" as ")[0] for n in node.children_by_field_name("name")] or [m]
        return [m]
    if t == "import_statement" and node.child_by_field_name("source") is None and not _strings(node, src):
        # python: import os, a.b as c
        return [_txt(c.child_by_field_name("name") or c, src) for c in node.children
                if c.type in ("dotted_name", "aliased_import")]
    s = _strings(node, src)
    if s:
        return s[:1] if t != "import_declaration" else s
    if t == "use_declaration":  # rust: use crate::a::b;
        return [_txt(node, src).removeprefix("use ").rstrip(";").split("{")[0].strip(": ")]
    module = node.child_by_field_name('module')
    if module is not None:
        return [_txt(module, src)]
    return []


def _bindings(node, src: bytes, line: int) -> list[tuple]:
    """Bindings explícitos de imports Python/JS/TS; não deduz aliases por semelhança."""
    out = []
    if node.type in ("import_statement", "import_from_statement") and node.child_by_field_name("source") is None:
        module = node.child_by_field_name("module_name")
        for child in node.children_by_field_name("name"):
            name = child.child_by_field_name("name") if child.type == "aliased_import" else child
            alias = child.child_by_field_name("alias")
            original = _txt(name, src)
            local = _txt(alias, src) if alias is not None else original.split(".")[0]
            if module is not None:
                mod = _txt(module, src)
                out.append((local, mod + original if not mod.strip(".") else mod,
                            None if not mod.strip(".") else original, line))
            else:
                out.append((local, original if alias is not None else original.split(".")[0], None, line))
        return out
    modules = _strings(node, src)
    if node.type != "import_statement" or not modules:
        return out
    module = modules[0]
    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        return out
    for child in clause.children:
        if child.type == "identifier":
            out.append((_txt(child, src), module, "default", line))
        elif child.type == "namespace_import":
            ids = [c for c in child.children if c.type == "identifier"]
            if ids:
                out.append((_txt(ids[-1], src), module, None, line))
        elif child.type == "named_imports":
            for spec in child.children:
                name = spec.child_by_field_name("name")
                alias = spec.child_by_field_name("alias")
                if name is not None:
                    out.append((_txt(alias if alias is not None else name, src), module, _txt(name, src), line))
    return out


def _parametros(node, src):
    """Nomes declarados como parâmetros; não confundir um callback com função global."""
    params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
    if params is None:
        return []
    names = []
    for param in params.named_children if params.type != "identifier" else [params]:
        name = param.child_by_field_name("name") or param.child_by_field_name("pattern")
        if name is None:
            name = param if param.type == "identifier" else next(
                (c for c in param.named_children if c.type == "identifier"), None)
        if name is not None and name.type == "identifier":
            names.append(_txt(name, src))
    return names


def extrair(src_text: str, ext: str) -> Estrutura | None:
    lang = LANGS.get(ext.lower())
    parser = _parser(lang) if lang else None
    if parser is None:
        return None
    src = src_text.encode("utf-8", "replace")
    tree = parser.parse(src)
    e = Estrutura()
    stack = [(tree.root_node, None)]  # (nó, símbolo que o contém)
    while stack:
        node, owner = stack.pop()
        t = node.type
        line = node.start_point[0] + 1
        cur = owner
        special = None
        if lang == 'elixir' and t == 'call':
            target = node.child_by_field_name('target')
            keyword = _txt(target, src) if target is not None else ''
            args = next((c for c in node.children if c.type == 'arguments'), None)
            if keyword in ('defmodule', 'def', 'defp', 'defmacro') and args is not None and args.named_children:
                head = args.named_children[0]
                name_node = head.child_by_field_name('target') if head.type == 'call' else head
                if name_node is not None:
                    special = (_txt(name_node, src), 'classe' if keyword == 'defmodule' else 'funcao')
        elif lang == 'r' and t == 'binary_operator':
            rhs, lhs = node.child_by_field_name('rhs'), node.child_by_field_name('lhs')
            if rhs is not None and rhs.type == 'function_definition' and lhs is not None and lhs.type == 'identifier':
                special = (_txt(lhs, src), 'funcao')
        if special or (t in DEF_TYPES and not (lang == 'r' and t == 'function_definition')
                       and not (t == 'let_binding' and not any(c.type == 'parameter' for c in node.children))):
            nome = special[0] if special else _name(node, src)
            if nome:
                kind = special[1] if special else ("classe" if any(k in t for k in CLASSY) else "funcao")
                e.simbolos.append((nome, kind, line, owner))
                cur = f"{owner}.{nome}" if owner else nome
                if kind == "funcao":
                    e.sombras.extend((cur, name, line) for name in _parametros(node, src))
                if kind == "classe":
                    sup = node.child_by_field_name("superclasses") or node.child_by_field_name("superclass")
                    heritage = [c for c in node.children if c.type in ("class_heritage", "extends_clause", "superclass",
                                                                       "super_interfaces", "base_list", "delegation_specifier")]
                    for h in ([sup] if sup is not None else []) + heritage:
                        for b in re.findall(r"[A-Za-z_][\w.]*", _txt(h, src)):
                            base = b.split(".")[-1]
                            if base not in ("extends", "implements", "object", "Object", nome):
                                e.herancas.append((cur, base, line))
        elif t == "variable_declarator" or t == "assignment":  # const f = () => … / f = lambda
            val = node.child_by_field_name("value") or node.child_by_field_name("right")
            n = node.child_by_field_name("name") or node.child_by_field_name("left")
            is_require = val is not None and val.type == "call_expression" and _callee(val, src) == "require"
            if n is not None and n.type == "identifier" and not is_require:
                e.sombras.append((owner, _txt(n, src), line))
            if owner is None and n is not None and is_require:
                mods = _strings(val, src)
                if mods and n.type == "identifier":
                    e.bindings.append((_txt(n, src), mods[0], None, line))
            if val is not None and val.type in ("arrow_function", "function_expression", "function", "lambda"):
                n = node.child_by_field_name("name") or node.child_by_field_name("left")
                if n is not None and n.type in ("identifier", "property_identifier"):
                    e.simbolos.append((_txt(n, src), "funcao", line, owner))
                    cur = f"{owner}.{_txt(n, src)}" if owner else _txt(n, src)
                    e.sombras.extend((cur, name, line) for name in _parametros(val, src))
        elif t == "export_statement" and owner is None and any(c.type == "default" for c in node.children):
            declaration = node.child_by_field_name("declaration") or node.child_by_field_name("value")
            if declaration is None:
                declaration = next((c for c in node.named_children if c.type in DEF_TYPES or c.type == "identifier"), None)
            if declaration is not None:
                name = _name(declaration, src) if declaration.type in DEF_TYPES else (
                    _txt(declaration, src) if declaration.type == "identifier" else None)
                if name:
                    e.exports.append(("default", name, line))
        elif t in IMPORT_TYPES:
            for m in _import_targets(node, src):
                e.imports.append((m, line))
            e.bindings.extend((f"{owner}::{local}" if owner else local, module, original, binding_line)
                              for local, module, original, binding_line in _bindings(node, src, line))
        elif t in CALL_TYPES:
            nome = _callee(node, src)
            if nome and nome.split(".")[-1] in ("require", "import") and "." not in nome and lang in ("javascript", "typescript", "tsx"):
                for s in _strings(node, src)[:1]:
                    e.imports.append((s, line))
            elif nome:
                is_signature = lang == 'julia' and node.parent is not None and node.parent.type == 'signature'
                if lang == 'elixir' and node.parent is not None and node.parent.type == 'arguments':
                    outer = node.parent.parent
                    target = outer.child_by_field_name('target') if outer is not None else None
                    is_signature = target is not None and _txt(target, src) in ('def', 'defp', 'defmacro')
                if not is_signature:
                    e.chamadas.append((owner, nome, line))
        elif "comment" in t:
            m = RATIONALE_RE.search(_txt(node, src))
            if m:
                e.porques.append((m.group(1).upper(), m.group(2).strip(" */#-"), line))
        for c in reversed(node.children):
            stack.append((c, cur))
    return e


# ---------------------------------------------------------------- resolução entre arquivos

CODE_KINDS = ("importa", "chama", "herda", "menciona")
_EXTS = tuple(LANGS)
_MENTION_EXTS = sorted({ext.lstrip('.') for ext in LANGS} | {'sql', 'yaml', 'yml', 'toml', 'json', 'css', 'scss', 'md'}, key=len, reverse=True)
MENCAO_RE = re.compile(r"(?<![\w/])((?:[\w@.-]+/)*[\w@.-]+\.(?:" + '|'.join(map(re.escape, _MENTION_EXTS)) + r"))\b")


def _ts_aliases(vault) -> list[tuple[str, list[str]]]:
    """paths do tsconfig/jsconfig: "@/*": ["./src/*"] → [("@/", ["src/"])]."""
    import json
    out = []
    for name in ("tsconfig.json", "jsconfig.json"):
        f = vault / name
        if not f.exists():
            continue
        try:
            raw = re.sub(r"//[^\n]*|/\*.*?\*/", "", f.read_text(errors="replace"), flags=re.S)
            raw = re.sub(r",(\s*[}\]])", r"\1", raw)
            opts = json.loads(raw).get("compilerOptions", {})
        except Exception:
            continue
        base = (opts.get("baseUrl") or ".").strip("./")
        for k, vs in (opts.get("paths") or {}).items():
            pref = k.rstrip("*")
            outs = [("/".join(x for x in (base, v.rstrip("*").lstrip("./")) if x)).rstrip("/") + "/" if v.rstrip("*") not in (".", "./") else (base + "/" if base else "") for v in vs]
            out.append((pref, [o.lstrip("/") for o in outs]))
    if not out:
        out = [("@/", ["", "src/"]), ("~/", ["", "src/"])]
    return out


def resolver_modulos(notes, vault):
    """Resolvedor de imports compartilhado pelos grafos de arquivos e símbolos."""
    import posixpath
    by_path = {p: i for i, p in notes.items()}
    by_noext = {}
    for i, p in notes.items():
        stem = posixpath.splitext(p)[0]
        by_noext.setdefault(stem, i)
        if posixpath.basename(stem) in ("index", "__init__", "mod"):
            by_noext.setdefault(posixpath.dirname(stem), i)
    aliases = _ts_aliases(vault)

    def mod_to_file(src_id, mod):
        src = notes[src_id]
        d = posixpath.dirname(src)
        cands = []
        if mod.startswith("."):
            if src.endswith(".py") and not mod.startswith(("./", "../")):  # python relativo: .db, ..pkg.x
                dots = len(mod) - len(mod.lstrip("."))
                base = d
                for _ in range(dots - 1):
                    base = posixpath.dirname(base)
                rest = mod.lstrip(".").replace(".", "/")
                cands.append(posixpath.join(base, rest) if rest else base)
            else:
                cands.append(posixpath.normpath(posixpath.join(d, mod)))
        else:
            for pref, targets in aliases:
                if pref and mod.startswith(pref):
                    cands += [t + mod[len(pref):] for t in targets]
            if src.endswith(".py") or "." in mod and "/" not in mod:
                parts = mod.replace(".", "/")
                cands += [parts, posixpath.join("src", parts)]
            cands.append(mod)
        for c in cands:
            c = c.lstrip("./") if not c.startswith("..") else c
            if c in by_path:
                return by_path[c]
            if c in by_noext:
                return by_noext[c]
            for ext in _EXTS:
                if c + ext in by_path:
                    return by_path[c + ext]
        return None

    return mod_to_file


def resolver(con, vault) -> dict:
    """Resolve imports/menções e projeta o grafo de símbolos em relações entre arquivos.
    Chamadas locais permanecem no grafo de símbolos, sem criar autoarestas de arquivos."""
    import posixpath
    from collections import defaultdict
    from .topologia import construir

    notes = {r["id"]: r["path"] for r in con.execute("SELECT id, path FROM notes")}
    by_path = {p: i for i, p in notes.items()}
    by_base = defaultdict(list)
    for i, p in notes.items():
        by_base[posixpath.basename(p).lower()].append(i)
    mod_to_file = resolver_modulos(notes, vault)
    edges = {}
    rank = {"EXTRACTED": 3, "INFERRED": 2, "AMBIGUOUS": 1}

    def add(src, dst, kind, conf, detail):
        if dst is None or src == dst:
            return
        edge = edges.setdefault((src, dst, kind), [conf, []])
        if rank[conf] > rank[edge[0]]:
            edge[0] = conf
        if detail and detail not in edge[1]:
            edge[1].append(detail)

    for r in con.execute("SELECT note_id, tipo, alvo FROM refs WHERE tipo IN ('import','menciona')"):
        if r["tipo"] == "import":
            add(r["note_id"], mod_to_file(r["note_id"], r["alvo"]), "importa", "EXTRACTED", r["alvo"])
        else:
            target = r["alvo"].removeprefix("./")
            if target in by_path:
                add(r["note_id"], by_path[target], "menciona", "EXTRACTED", target)
            else:
                candidates = by_base.get(posixpath.basename(target).lower(), [])
                if len(candidates) == 1:
                    add(r["note_id"], candidates[0], "menciona", "INFERRED", target)
    con.execute("DELETE FROM links WHERE kind IN ('importa','menciona','chama','herda')")

    def save(items):
        con.executemany("INSERT INTO links(src, target, dst, kind, weight, conf, detalhe) VALUES(?,?,?,?,?,?,?)",
            [(src, notes[dst], dst, kind, min(1.0, 0.4 + 0.1 * len(details)), conf,
              ", ".join(details[:4]) + (f" (+{len(details) - 4})" if len(details) > 4 else ""))
             for (src, dst, kind), (conf, details) in items.items()])

    save(edges)
    topology = construir(con, vault)
    symbols = {}
    for a, b, attrs in topology.edges(data=True):
        if attrs["kind"] not in ("chama", "herda"):
            continue
        src = topology.nodes[a].get("arquivo", a)
        dst = topology.nodes[b].get("arquivo", b)
        if src == dst:
            continue
        key = (src, dst, attrs["kind"])
        item = symbols.setdefault(key, [attrs["conf"], []])
        if rank[attrs["conf"]] > rank[item[0]]:
            item[0] = attrs["conf"]
        label = topology.nodes[b]["label"] + ("()" if attrs["kind"] == "chama" else "")
        if label not in item[1]:
            item[1].append(label)
    save(symbols)
    return {"arestas_codigo": len(edges) + len(symbols), "chamadas_ambiguas": len(topology.graph["ambiguas"])}


_COMENTARIO_RE = re.compile(r'("""|\'\'\')(.*?)\1|/\*\*?(.*?)\*/|((?:^[ \t]*(?://|#)(?!!).*\n?){2,})', re.S | re.M)


def palavras_do_caminho(rel: str) -> str:
    """lib/ai/promptHardening.ts → "lib ai prompt hardening" (camelCase e _ viram palavras)."""
    base = re.sub(r"\.[^./]+$", "", rel)
    base = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base)
    return " ".join(w.lower() for w in re.split(r"[/_\-.\s]+", base) if w)


def resumo_arquivo(rel: str, texto: str, simbolos: list[str]) -> str:
    """Caminho em palavras + o primeiro comentário de propósito do arquivo + nomes definidos."""
    doc = ""
    for m in _COMENTARIO_RE.finditer(texto[:6000]):
        bloco = m.group(2) or m.group(3) or m.group(4) or ""
        bloco = re.sub(r"^[ \t]*(?:\*|//|#)+ ?", "", bloco, flags=re.M)
        bloco = " ".join(bloco.split())
        if len(bloco) >= 40 and not bloco.lower().startswith(("eslint", "@ts-", "prettier", "copyright")):
            doc = bloco[:900]
            break
    nomes = ", ".join(dict.fromkeys(simbolos))[:400]
    return f"{palavras_do_caminho(rel)}\n{doc}\nDefine: {nomes}".strip()
