---
title: "Compatibilidade com Obsidian e execução offline"
tipo: "decisao"
data: "2026-09-29"
status: "ativa"
projeto: "[[segundo-cerebro]]"
origem: "Claude Code · segundo-cerebro"
tags: ["decisao", "markdown", "obsidian", "offline", "portabilidade"]
---

# Compatibilidade com Obsidian e execução offline

## Decisão

O segundo cérebro deve funcionar como uma pasta comum de arquivos Markdown, com interface própria de grafo e sem dependência de CDN, mantendo compatibilidade para abrir e editar o conteúdo no Obsidian.

## Contexto

A interface do grafo, o leitor de notas e a navegação por links foram testados no navegador sem erros no console.

## Por quê

Preservar a portabilidade dos dados e permitir alternar entre a interface própria e o Obsidian.

## Alternativas consideradas

- Adotar um formato ou banco de dados proprietário
- Depender exclusivamente do Obsidian

## Consequências

Os dados continuam legíveis e editáveis fora do sistema, inclusive em outras ferramentas que suportem Markdown.
