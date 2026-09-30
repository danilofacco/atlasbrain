"""Recarga a quente: processos de vida longa (o MCP dentro das sessões do Claude/Codex) passam a rodar a
versão nova do atlasbrain assim que o código muda no disco, sem o cliente perder a conexão.

Por que existe: um processo com código antigo reindexava o projeto no formato antigo por cima do novo
(aconteceu duas vezes em 29/09/2026), e fechar todas as sessões para atualizar não é algo que se lembra
de fazer. Aqui, entre uma chamada e outra, o processo compara a "impressão digital" dos arquivos do
pacote e, se mudou, recarrega os módulos na ordem das dependências.

Limite honesto: ferramentas NOVAS (nome ou parâmetros novos) só aparecem ao reabrir a sessão, porque o
cliente guarda a lista de ferramentas. Correções e mudanças de comportamento valem na hora.
"""

import importlib
import sys
import threading
import time
from pathlib import Path

PACKAGE = Path(__file__).parent
# ordem de dependência: quem é importado vem antes de quem importa
MODULES = ["localization", "mcp_api", "config", "db", "embed", "parse", "extract", "code", "relevance", "search", "graph",
           "queries", "topology", "intelligence", "editor", "indexer", "memory", "url_import", "report", "capture", "classify", "consolidation", "rename", "bench", "tools"]


def version(include_interface: bool = False) -> tuple:
    """Impressão digital do código: (mtime mais recente, quantidade de arquivos)."""
    files = list(PACKAGE.glob("*.py")) + (list((PACKAGE / "static").glob("*")) if include_interface else [])
    return max(f.stat().st_mtime for f in files), len(files)


def syntax_error() -> str | None:
    """Erro de sintaxe no código atual do pacote (ou None). Evita reiniciar para uma versão quebrada."""
    import py_compile
    for f in PACKAGE.glob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True, cfile=None)
        except py_compile.PyCompileError as e:
            return str(e)
    return None


def watch_and_restart(before_restart, log, interval: float = 3.0) -> None:
    """Para processos que podem reiniciar (a interface web): quando o código muda e compila, troca o
    processo pelo novo no mesmo lugar (mesmos argumentos, sem abrir outra aba do navegador)."""
    import os

    def watcher():
        v = version()
        while True:
            time.sleep(interval)
            if version() == v:
                continue
            error = syntax_error()
            if error:
                log(f"[atlasbrain] código novo com erro; sigo na versão atual: {error.splitlines()[-1]}")
                v = version()
                continue
            log("[atlasbrain] código atualizado: reiniciando a interface")
            before_restart()
            args = [a for a in sys.argv[1:] if a not in ("--no-open", "--nao-abrir")] + ["--no-open"]
            os.execv(sys.executable, [sys.executable, "-m", "atlasbrain.cli", *args])

    threading.Thread(target=watcher, daemon=True).start()


def current_module(name: str):
    """Sempre a versão atual do módulo (use isto em vez de guardar funções importadas)."""
    return importlib.import_module(f"atlasbrain.{name}")


class ModuleReloader:
    def __init__(self, on_reload=None, interval: float = 3.0, log=None):
        self.v = version()
        self.on_reload = on_reload
        self.interval = interval
        self.last_check = 0.0
        self.reload_count = 0
        self.lock = threading.Lock()
        self.log = log or (lambda *_: None)

    def refresh(self) -> bool:
        """Recarrega se o código mudou. Barato: no máximo um stat de ~20 arquivos a cada `intervalo` s."""
        now = time.time()
        if now - self.last_check < self.interval:
            return False
        with self.lock:
            self.last_check = now
            v = version()
            if v == self.v:
                return False
            try:
                previous_embed_module = sys.modules.get("atlasbrain.embed")
                model = getattr(previous_embed_module, "_model", None)
                for name in MODULES:
                    m = sys.modules.get(f"atlasbrain.{name}")
                    if m is not None:
                        importlib.reload(m)
                current_embed_module = sys.modules.get("atlasbrain.embed")
                if model is not None and current_embed_module is not None and current_embed_module._model is None:
                    current_embed_module._model = model  # não recarrega o modelo de embeddings (~1 s) à toa
            except Exception as e:  # código no meio de uma edição: tenta de novo na próxima chamada
                self.log(f"[atlasbrain] recarga adiada, código com erro: {e}")
                return False
            self.v = v
            self.reload_count += 1
            self.log(f"[atlasbrain] código atualizado: recarreguei os módulos (recarga #{self.reload_count})")
            if self.on_reload:
                self.on_reload()
            return True
