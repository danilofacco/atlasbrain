import subprocess
import shutil
from pathlib import Path

import pytest

from atlasbrain import config, embed


@pytest.fixture(scope="session")
def parser_cache(tmp_path_factory):
    # Grammar libraries are downloaded on demand. Preserve cached libraries in a
    # disposable directory before tests change HOME; never write to the user cache.
    from tree_sitter_language_pack import PackConfig, cache_dir, configure
    source = Path(cache_dir())
    isolated = tmp_path_factory.mktemp("parser-cache")
    configure(PackConfig(cache_dir=str(isolated)))
    target = Path(cache_dir())
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)
    yield target
    configure()


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch, parser_cache):
    """Nenhum teste toca no registro real, no cérebro global nem na home do usuário."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "REGISTRY", tmp_path / "registro" / "projects.json")
    monkeypatch.setattr(config, "GLOBAL_BRAIN", home / "SegundoCerebro")
    config._registered.clear()
    monkeypatch.setattr(embed, "EMBED_ENABLED", False)  # rápido por padrão; ver fixture com_embeddings
    monkeypatch.delenv("ATLASBRAIN_VAULT", raising=False)
    yield


@pytest.fixture
def com_embeddings(monkeypatch):
    monkeypatch.setattr(embed, "EMBED_ENABLED", True)


ARQUIVOS = {
    ".gitignore": "build/\n",
    "tsconfig.json": '{ // comentário permitido\n "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["./*"] } } }\n',
    "src/__init__.py": "",
    "src/app.py": (
        "import re\n"
        "from .util import soma\n"
        "from . import db\n\n"
        "def main():\n"
        "    # WHY: ponto de entrada do exemplo\n"
        "    re.search('a', 'b')\n"
        "    banco = db.Banco()\n"
        "    return soma(1, 2)\n"
    ),
    "src/util.py": "def soma(a, b):\n    return a + b\n",
    "src/db.py": "class Banco:\n    def conectar(self):\n        return True\n",
    "src/busca.py": "def search(q):\n    return [q]\n",
    "lib/api.ts": "export const api = () => fetch('/x');\nexport class Base {}\n",
    "lib/a.ts": "export function enviar() { return 1 }\n",
    "lib/b.ts": "export function enviar() { return 2 }\n",
    "web/helpers.ts": "export function ajuda() { return 0 }\n",
    "web/tela.ts": (
        'import { api } from "@/lib/api";\n'
        'import { ajuda } from "./helpers";\n'
        "const carregar = () => api();\n"
        "export class Tela extends Base { mostrar() { ajuda(); enviar(); } }\n"
    ),
    "notas/Arquitetura.md": "---\ntags: [arquitetura]\n---\n# Arquitetura\nO cálculo fica em `src/util.py`. Ver [[Roadmap]] e [[Glossário]].\n#backend\n",
    "notas/Glossário.md": "# Glossário\nTermos do projeto. Volta para [[Arquitetura]].\n",
    "build/gerado.py": "def nao_deve_indexar():\n    pass\n",
}


@pytest.fixture
def project(tmp_path) -> Path:
    """Repositório git pequeno com Python, TypeScript (alias @/), notas e uma pasta ignorada."""
    root = tmp_path / "projeto"
    for rel, txt in ARQUIVOS.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(txt, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root.resolve()


@pytest.fixture
def indexado(project):
    from atlasbrain.db import connect
    from atlasbrain.indexer import index_vault

    index_vault(project, quiet=True)
    con = connect(project)
    yield project, con
    con.close()


def aresta(con, origin: str, destination: str, kind: str):
    """Linha da aresta origem→destino (ou None)."""
    return con.execute(
        "SELECT l.kind, l.conf, l.detalhe FROM links l JOIN notes a ON a.id=l.src JOIN notes b ON b.id=l.dst "
        "WHERE a.path=? AND b.path=? AND l.kind=?", (origin, destination, kind)).fetchone()
