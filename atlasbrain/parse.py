"""Frontmatter, título, [[wikilinks]], links markdown, #tags e fatiamento em trechos."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import yaml

WIKILINK_RE = re.compile(r"!?\[\[([^\]\|#\n]+)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]")
MDLINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
TAG_RE = re.compile(r"(?<![\w/#&;:])#([\wÀ-ÿ][\wÀ-ÿ/\-]*)")
FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Parsed:
    title: str
    frontmatter: dict
    body: str
    aliases: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    links: list[tuple[str, str]] = field(default_factory=list)  # (alvo, tipo)
    chunks: list[tuple[str, str]] = field(default_factory=list)  # (cabeçalho, texto)


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        fm = yaml.safe_load(text[3:end]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    nl = text.find("\n", end + 4)
    return fm, text[nl + 1:] if nl != -1 else ""


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in re.split(r"[,\s]+", v) if s.strip()]
    if isinstance(v, (list, tuple)):
        return [str(s).strip() for s in v if str(s).strip()]
    return [str(v)]


def _is_local_link(url: str) -> bool:
    return not (
        "://" in url or url.startswith(("mailto:", "#", "tel:", "data:"))
    )


def chunk_markdown(body: str, max_chars: int = 1200, overlap: int = 150) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    path: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            if current:
                sections.append((" › ".join(path), current))
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((" › ".join(path), current))

    chunks = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        if len(text) <= max_chars:
            chunks.append((heading, text))
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            if end < len(text):
                cut = text.rfind("\n", start + max_chars // 2, end)
                if cut == -1:
                    cut = text.rfind(" ", start + max_chars // 2, end)
                end = cut if cut != -1 else end
            chunks.append((heading, text[start:end].strip()))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return chunks


def chunk_code(text: str, name: str, lines_per_chunk: int = 60, overlap: int = 8) -> list[tuple[str, str]]:
    lines = text.splitlines()
    out = []
    step = lines_per_chunk - overlap
    for i in range(0, max(len(lines), 1), step):
        part = "\n".join(lines[i : i + lines_per_chunk]).strip()
        if part:
            out.append((f"{name} (linhas {i + 1}-{min(i + lines_per_chunk, len(lines))})", part))
        if i + lines_per_chunk >= len(lines):
            break
    return out


def parse(text: str, path: Path, kind: str) -> Parsed:
    fm, body = split_frontmatter(text) if kind == "nota" else ({}, text)

    title = str(fm.get("title") or fm.get("titulo") or "").strip()
    if not title and kind != "codigo":
        for line in body.splitlines()[:40]:
            m = HEADING_RE.match(line)
            if m and len(m.group(1)) == 1:
                title = m.group(2).strip()
                break
    title = title or path.stem

    p = Parsed(title=title, frontmatter=fm, body=body)
    p.aliases = _as_list(fm.get("aliases") or fm.get("alias"))
    p.tags = {t.lstrip("#").lower() for t in _as_list(fm.get("tags") or fm.get("tag"))}

    if kind == "codigo":
        p.chunks = chunk_code(body, path.name)
        return p

    clean = INLINE_CODE_RE.sub("", FENCE_RE.sub("", body))
    for m in WIKILINK_RE.finditer(clean):
        p.links.append((m.group(1).strip(), "wikilink"))
    for m in MDLINK_RE.finditer(clean):
        url = m.group(1).strip("<>")
        if _is_local_link(url):
            p.links.append((unquote(url.split("#")[0]), "mdlink"))
    for m in TAG_RE.finditer(clean):
        tag = m.group(1).rstrip("/-").lower()
        if tag and not tag.isdigit():
            p.tags.add(tag)
    for k, v in fm.items():  # propriedades estilo Obsidian: related: "[[Outra nota]]"
        for item in _as_list(v) if isinstance(v, (list, tuple)) else [v]:
            if isinstance(item, str):
                for m in WIKILINK_RE.finditer(item):
                    p.links.append((m.group(1).strip(), "wikilink"))

    p.chunks = chunk_markdown(body)
    if not p.chunks and title:
        p.chunks = [("", title)]
    return p
