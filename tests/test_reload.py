import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

from atlasbrain import reload

RAIZ = Path(__file__).resolve().parent.parent


def test_versao_muda_quando_um_arquivo_muda(tmp_path, monkeypatch):
    pkg = tmp_path / "atlasbrain"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(reload, "PACKAGE", pkg)
    v1 = reload.version()
    os.utime(pkg / "a.py", (time.time() + 10, time.time() + 10))
    assert reload.version() != v1
    (pkg / "b.py").write_text("")
    assert reload.version()[1] == 2


def _editar(file_path: Path, before: str, depois: str):
    file_path.write_text(file_path.read_text().replace(before, depois))
    t = time.time() + 5  # garante mtime maior mesmo em sistemas de arquivos com resolução de 1-2 s
    os.utime(file_path, (t, t))


def test_sessao_mcp_aberta_recebe_codigo_novo_sem_reconectar(indexado, tmp_path):
    """Com a sessão aberta, muda o código no disco: a próxima chamada já roda a versão nova. Código
    quebrado no meio da edição não derruba o processo, que segue na versão anterior até o conserto."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project, _ = indexado
    copia = tmp_path / "pkg"
    shutil.copytree(RAIZ / "atlasbrain", copia / "atlasbrain", ignore=shutil.ignore_patterns("__pycache__"))
    ferr = copia / "atlasbrain" / "tools.py"

    async def run_benchmark():
        env = {"PYTHONPATH": str(copia), "ATLASBRAIN_NO_EMBED": "1", "HOME": str(Path.home()),
               "PATH": "/usr/bin:/bin:/opt/homebrew/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        p = StdioServerParameters(command=sys.executable, args=["-m", "atlasbrain.cli", "mcp", "--sem-auto"],
                                  cwd=str(project), env=env)
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                invoke = lambda: s.call_tool("onde", {"simbolo": "soma"})
                before = (await invoke()).content[0].text

                _editar(ferr, "**Definição de", "**VERSÃO NOVA · Definição de")
                await asyncio.sleep(3.2)  # intervalo mínimo entre checagens
                depois = (await invoke()).content[0].text

                _editar(ferr, "def symbol_location(ctx,", "def symbol_location(ctx,,")  # erro de sintaxe no meio da edição
                await asyncio.sleep(3.2)
                quebrado = (await invoke()).content[0].text

                _editar(ferr, "def symbol_location(ctx,,", "def symbol_location(ctx,")
                _editar(ferr, "**VERSÃO NOVA · Definição de", "**CONSERTADO · Definição de")
                await asyncio.sleep(3.2)
                consertado = (await invoke()).content[0].text
                return before, depois, quebrado, consertado

    before, depois, quebrado, consertado = asyncio.run(run_benchmark())
    assert before.startswith("**Definição de") and "src/util.py" in before
    assert depois.startswith("**VERSÃO NOVA")          # mesma sessão, código novo
    assert quebrado.startswith("**VERSÃO NOVA")        # código quebrado: segue na última versão boa
    assert consertado.startswith("**CONSERTADO")       # conserto entra sozinho
