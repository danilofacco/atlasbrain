---
title: "Poll external project changes once per minute"
tipo: "decisao"
data: "2026-10-01"
status: "ativa"
origem: "conversa"
tags: ["decisao", "indexing", "performance"]
relevancia: 0.805
---

# Poll external project changes once per minute

## Decisão

O serviço compartilhado verifica alterações externas nos projetos uma vez por minuto (espera de 60 segundos entre ciclos), conforme solicitado pelo usuário. O IndexQueue do serviço usa settle=0 para processar alterações no mesmo ciclo em que são detectadas, sem exigir mais um minuto de espera.

## Contexto

A varredura anterior rodava a cada 3 segundos, disparando consultas Git repetidamente mesmo sem alterações. O usuário prefere reduzir esse volume para uma verificação por minuto.

## Por quê

Reduzir de aproximadamente 20 para 1 ciclo por minuto por projeto em repouso, aceitando a latência de detecção de até cerca de um minuto em projetos pequenos.

## Consequências

Escritas explícitas pelo MCP e editor mantêm atualização imediata. A reconciliação periódica de cinco minutos e as tentativas após indexação ignorada/falha são preservadas. O desligamento continua interrompendo a espera por meio do threading.Event, sem aguardar o minuto inteiro. Projetos grandes e várias indexações podem prolongar o ciclo.
