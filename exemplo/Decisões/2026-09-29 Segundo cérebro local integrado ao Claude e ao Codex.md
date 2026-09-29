---
title: "Segundo cérebro local integrado ao Claude e ao Codex"
tipo: "decisao"
data: "2026-09-29"
status: "ativa"
projeto: "[[segundo-cerebro]]"
origem: "Claude Code · segundo-cerebro"
tags: ["arquitetura", "claude", "codex", "decisao", "local", "mcp", "privacidade"]
---

# Segundo cérebro local integrado ao Claude e ao Codex

## Decisão

O segundo cérebro será uma pasta local em ~/SegundoCerebro, conectada ao Claude Code e ao Codex por meio de um servidor MCP.

## Contexto

O sistema foi implementado e testado com busca híbrida, grafo de relações, leitura de notas, backlinks, criação de notas e navegação por caminhos.

## Por quê

Permitir que Claude e Codex consultem decisões e informações do usuário sem enviar os dados para fora da máquina.

## Alternativas consideradas

- Usar Obsidian e Graphify como solução principal
- Usar um serviço de conhecimento hospedado na nuvem

## Consequências

A pasta permanece compatível com o Obsidian, pode receber markdowns, PDFs, DOCX e código, e o índice é atualizado automaticamente a cada minuto.
