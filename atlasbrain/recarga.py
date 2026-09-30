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

PACOTE = Path(__file__).parent
# ordem de dependência: quem é importado vem antes de quem importa
MODULOS = ["localization", "mcp_api", "config", "db", "embed", "parse", "extract", "codigo", "relevancia", "search", "graph",
           "consultas", "topologia", "inteligencia", "editor", "indexer", "registro", "importacao", "relatorio", "captura", "classify", "consolidacao", "renomear", "bench", "ferramentas"]


def versao(com_interface: bool = False) -> tuple:
    """Impressão digital do código: (mtime mais recente, quantidade de arquivos)."""
    arqs = list(PACOTE.glob("*.py")) + (list((PACOTE / "static").glob("*")) if com_interface else [])
    return max(f.stat().st_mtime for f in arqs), len(arqs)


def compila() -> str | None:
    """Erro de sintaxe no código atual do pacote (ou None). Evita reiniciar para uma versão quebrada."""
    import py_compile
    for f in PACOTE.glob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True, cfile=None)
        except py_compile.PyCompileError as e:
            return str(e)
    return None


def vigiar_e_reiniciar(antes_de_reiniciar, log, intervalo: float = 3.0) -> None:
    """Para processos que podem reiniciar (a interface web): quando o código muda e compila, troca o
    processo pelo novo no mesmo lugar (mesmos argumentos, sem abrir outra aba do navegador)."""
    import os

    def vigia():
        v = versao()
        while True:
            time.sleep(intervalo)
            if versao() == v:
                continue
            erro = compila()
            if erro:
                log(f"[atlasbrain] código novo com erro; sigo na versão atual: {erro.splitlines()[-1]}")
                v = versao()
                continue
            log("[atlasbrain] código atualizado: reiniciando a interface")
            antes_de_reiniciar()
            args = [a for a in sys.argv[1:] if a != "--nao-abrir"] + ["--nao-abrir"]
            os.execv(sys.executable, [sys.executable, "-m", "atlasbrain.cli", *args])

    threading.Thread(target=vigia, daemon=True).start()


def modulo(nome: str):
    """Sempre a versão atual do módulo (use isto em vez de guardar funções importadas)."""
    return importlib.import_module(f"atlasbrain.{nome}")


class Recarregador:
    def __init__(self, ao_recarregar=None, intervalo: float = 3.0, log=None):
        self.v = versao()
        self.ao_recarregar = ao_recarregar
        self.intervalo = intervalo
        self.ultimo = 0.0
        self.recargas = 0
        self.lock = threading.Lock()
        self.log = log or (lambda *_: None)

    def fresco(self) -> bool:
        """Recarrega se o código mudou. Barato: no máximo um stat de ~20 arquivos a cada `intervalo` s."""
        agora = time.time()
        if agora - self.ultimo < self.intervalo:
            return False
        with self.lock:
            self.ultimo = agora
            v = versao()
            if v == self.v:
                return False
            try:
                embed_antigo = sys.modules.get("atlasbrain.embed")
                modelo = getattr(embed_antigo, "_model", None)
                for nome in MODULOS:
                    m = sys.modules.get(f"atlasbrain.{nome}")
                    if m is not None:
                        importlib.reload(m)
                novo_embed = sys.modules.get("atlasbrain.embed")
                if modelo is not None and novo_embed is not None and novo_embed._model is None:
                    novo_embed._model = modelo  # não recarrega o modelo de embeddings (~1 s) à toa
            except Exception as e:  # código no meio de uma edição: tenta de novo na próxima chamada
                self.log(f"[atlasbrain] recarga adiada, código com erro: {e}")
                return False
            self.v = v
            self.recargas += 1
            self.log(f"[atlasbrain] código atualizado: recarreguei os módulos (recarga #{self.recargas})")
            if self.ao_recarregar:
                self.ao_recarregar()
            return True
