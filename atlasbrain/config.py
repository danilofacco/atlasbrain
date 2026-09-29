import json
import os
import subprocess
import time
from pathlib import Path

from .codigo import LANGS

BRAIN = ".atlasbrain"  # pasta do cérebro dentro de cada projeto (como .git ou .claude)
GLOBAL_BRAIN = Path(os.environ.get("ATLASBRAIN_GLOBAL", "~/SegundoCerebro")).expanduser()
REGISTRY = Path.home() / ".config" / "atlasbrain" / "projetos.json"

IGNORE_DIRS = {
    ".git", ".obsidian", ".cerebro", ".trash", "node_modules", ".venv", "venv",
    "__pycache__", "dist", "build", ".next", ".idea", ".vscode",
}
TEXT_EXT = {".md", ".markdown", ".txt", ".org", ".rst"}
CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
    ".swift", ".kt", ".c", ".cpp", ".h", ".cs", ".sh", ".sql", ".lua",
    ".yaml", ".yml", ".toml", ".css", ".scss",
}
# A lista de parsers é a fonte da cobertura: nenhum parser fica inacessível no scan.
CODE_EXT |= set(LANGS)
DOC_EXT = {".pdf", ".docx", ".html", ".htm"}
ALL_EXT = TEXT_EXT | CODE_EXT | DOC_EXT

MAX_TEXT_BYTES = 2_000_000
MAX_DOC_BYTES = 50_000_000

EMBED_MODEL = os.environ.get(
    "ATLASBRAIN_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
EMBED_ENABLED = os.environ.get("ATLASBRAIN_NO_EMBED", "") == ""
MODEL_CACHE = Path.home() / ".cache" / "atlasbrain" / "models"

# Ligações semânticas: cada nota ganha até K vizinhas com similaridade >= limiar.
SIMILAR_K = int(os.environ.get("ATLASBRAIN_SIMILAR_K", "3"))
SIMILAR_MIN = float(os.environ.get("ATLASBRAIN_SIMILAR_MIN", "0.6"))

_GITIGNORE = "# índice local do atlasbrain (as notas em markdown são versionadas)\nindex.db*\n.editor-backups/\n*.lock\ncaptura.*\nRELATORIO.md\n"


def kind_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        return "nota"
    if ext in CODE_EXT:
        return "codigo"
    if ext in DOC_EXT:
        return "documento"
    return None


def git_root(start: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def find_project(start: Path) -> Path | None:
    """Raiz do projeto a partir de uma pasta: a `.atlasbrain/` mais próxima subindo, senão a raiz do git."""
    start = start.expanduser().resolve()
    home = Path.home().resolve()
    for p in [start, *start.parents]:
        if p == home or p == p.parent:
            break
        if (p / BRAIN).is_dir():
            return p
    return git_root(start)


def resolve_vault(arg: str | None = None, cwd: str | None = None) -> Path:
    """--vault > $ATLASBRAIN_VAULT > projeto da pasta atual > cérebro global (~/SegundoCerebro)."""
    v = arg or os.environ.get("ATLASBRAIN_VAULT")
    if v:
        p = Path(v).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"Pasta não encontrada: {p}")
        return p
    proj = find_project(Path(cwd or os.getcwd()))
    if proj:
        return proj
    GLOBAL_BRAIN.mkdir(parents=True, exist_ok=True)
    return GLOBAL_BRAIN.resolve()


def is_global(vault: Path) -> bool:
    return vault.resolve() == GLOBAL_BRAIN.resolve()


def data_dir(vault: Path) -> Path:
    d = vault / BRAIN
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    atual = gi.read_text() if gi.exists() else ""
    faltam = [l for l in _GITIGNORE.splitlines() if l and not l.startswith("#") and l not in atual.splitlines()]
    if faltam:  # cérebros antigos ganham as regras novas
        gi.write_text((atual or _GITIGNORE.splitlines()[0] + "\n") + "".join(l + "\n" for l in faltam))
    register(vault)
    return d


def notes_dir(vault: Path) -> Path:
    """Onde decisões/aprendizados são gravados: dentro de .atlasbrain/ num projeto; na raiz no cérebro global."""
    return vault if is_global(vault) else vault / BRAIN


_registered: set[str] = set()


def register(vault: Path) -> None:
    key = str(vault.resolve())
    if key in _registered:
        return
    _registered.add(key)
    try:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
        reg[key] = {"nome": vault.name, "visto": time.time(), "global": is_global(vault)}
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2))
    except (OSError, ValueError):
        pass


def registered() -> list[dict]:
    try:
        reg = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return []
    out = [{"path": k, **v} for k, v in reg.items() if Path(k, BRAIN).is_dir()]
    return sorted(out, key=lambda x: (not x.get("global"), -x.get("visto", 0)))


def unregister(vault: Path) -> None:
    key = str(vault.resolve())
    _registered.discard(key)
    try:
        reg = json.loads(REGISTRY.read_text())
        reg.pop(key, None)
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2))
    except (OSError, ValueError):
        pass


PROIBIDAS = [Path("/"), Path("/System"), Path("/Library"), Path("/Applications"), Path("/usr"), Path("/private"),
             Path("/Volumes"), Path("/Users")]


def pasta_invalida(p: Path) -> str | None:
    """Motivo para recusar uma pasta escolhida à mão (ou None se ela pode virar cérebro)."""
    if not p.exists():
        return "Essa pasta não existe."
    if not p.is_dir():
        return "Isso é um arquivo, não uma pasta."
    home = Path.home().resolve()
    if p == home or p in home.parents or p in PROIBIDAS:
        return "Pasta ampla demais (home, raiz ou pasta do sistema). Escolha a pasta de um projeto."
    if any(p == x or x in p.parents for x in (Path("/System"), Path("/usr"), home / "Library")):
        return "Pasta de sistema: escolha a pasta de um projeto."
    return None
