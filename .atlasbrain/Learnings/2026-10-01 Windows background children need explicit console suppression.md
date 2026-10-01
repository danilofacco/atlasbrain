---
title: "Windows background children need explicit console suppression"
tipo: "aprendizado"
data: "2026-10-01"
origem: "conversa"
tags: ["aprendizado", "desktop", "subprocess", "windows"]
relevancia: 0.865
---

# Windows background children need explicit console suppression

O serviço nativo inicia por pythonw.exe, mas redirecionar stdout/stderr dos filhos não impede o Windows de abrir consoles. O loop de indexação em atlasbrain/service.py chama scan a cada 3 segundos; atlasbrain/indexer.py executava git ls-files sem CREATE_NO_WINDOW, gerando uma causa compatível com o relato de janelas piscando. config.git_root, update._run/merge-base e capture._extract também precisavam da política. Centralizar CREATE_NO_WINDOW em processes.hidden_options e aplicar às chamadas internas; background_options combina essa opção com CREATE_NEW_PROCESS_GROUP. No macOS/Linux hidden_options retorna vazio. O seletor Windows usa PowerShell 5.1 -NoProfile -STA e WinForms FolderBrowserDialog com console suprimido, expondo apenas a seleção de pasta. Testes incluem consultas Git repetidas, comandos de atualização/captura, e verificação nativa GetConsoleWindow()==0 no CI Windows; o diálogo é construído e seu script validado sem interação modal no CI. Fonte técnica: https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags.
