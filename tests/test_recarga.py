import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

from atlasbrain import recarga

RAIZ = Path(__file__).resolve().parent.parent


def test_versao_muda_quando_um_arquivo_muda(tmp_path, monkeypatch):
    pkg = tmp_path / "atlasbrain"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(recarga, "PACOTE", pkg)
    v1 = recarga.versao()
    os.utime(pkg / "a.py", (time.time() + 10, time.time() + 10))
    assert recarga.versao() != v1
    (pkg / "b.py").write_text("")
    assert recarga.versao()[1] == 2


def _editar(arquivo: Path, antes: str, depois: str):
    arquivo.write_text(arquivo.read_text().replace(antes, depois))
    t = time.time() + 5  # garante mtime maior mesmo em sistemas de arquivos com resolução de 1-2 s
    os.utime(arquivo, (t, t))


def test_sessao_mcp_aberta_recebe_codigo_novo_sem_reconectar(indexado, tmp_path):
    """Com a sessão aberta, muda o código no disco: a próxima chamada já roda a versão nova. Código
    quebrado no meio da edição não derruba o processo, que segue na versão anterior até o conserto."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    projeto, _ = indexado
    copia = tmp_path / "pkg"
    shutil.copytree(RAIZ / "atlasbrain", copia / "atlasbrain", ignore=shutil.ignore_patterns("__pycache__"))
    ferr = copia / "atlasbrain" / "ferramentas.py"

    async def rodar():
        env = {"PYTHONPATH": str(copia), "ATLASBRAIN_NO_EMBED": "1", "HOME": str(Path.home()),
               "PATH": "/usr/bin:/bin:/opt/homebrew/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        p = StdioServerParameters(command=sys.executable, args=["-m", "atlasbrain.cli", "mcp", "--sem-auto"],
                                  cwd=str(projeto), env=env)
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                chamar = lambda: s.call_tool("onde", {"simbolo": "soma"})
                antes = (await chamar()).content[0].text

                _editar(ferr, "**Definição de", "**VERSÃO NOVA · Definição de")
                await asyncio.sleep(3.2)  # intervalo mínimo entre checagens
                depois = (await chamar()).content[0].text

                _editar(ferr, "def onde(ctx,", "def onde(ctx,,")  # erro de sintaxe no meio da edição
                await asyncio.sleep(3.2)
                quebrado = (await chamar()).content[0].text

                _editar(ferr, "def onde(ctx,,", "def onde(ctx,")
                _editar(ferr, "**VERSÃO NOVA · Definição de", "**CONSERTADO · Definição de")
                await asyncio.sleep(3.2)
                consertado = (await chamar()).content[0].text
                return antes, depois, quebrado, consertado

    antes, depois, quebrado, consertado = asyncio.run(rodar())
    assert antes.startswith("**Definição de") and "src/util.py" in antes
    assert depois.startswith("**VERSÃO NOVA")          # mesma sessão, código novo
    assert quebrado.startswith("**VERSÃO NOVA")        # código quebrado: segue na última versão boa
    assert consertado.startswith("**CONSERTADO")       # conserto entra sozinho
