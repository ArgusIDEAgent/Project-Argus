# AI Developer Knowledge Assistant

> An AI-powered engineering assistant that understands *your* organization's own codebase — not just code in general.

## Table of Contents

- [Description](#description)
- [Key Features](#key-features)

## Description

Onboarding a new developer into a large, mature codebase is one of the most expensive — and least enjoyable — parts of software engineering. New hires don't yet know the architecture, the dependency graph, the internal APIs, the team's coding standards, or the history of past bugs. Until they do, senior engineers end up spending enormous amounts of time re-explaining the same things over and over again.

**AI Developer Knowledge Assistant** is an internal engineering assistant that fixes this by actually understanding *your organization's* codebase — not just code in general. Unlike a generic chat assistant, it combines Retrieval-Augmented Generation (RAG), a vector database, AST-based static analysis, and a knowledge graph of your code's structure to answer deep, codebase-specific questions such as:

- "Where is JWT authentication implemented?"
- "Which services call this API?"
- "What happens if I modify this function?"
- "Show me related bugs."
- "Show me the dependency graph."
- "Explain this module."

By integrating directly with Git and Jira, and shipping as a native VS Code extension, it puts this knowledge exactly where developers already spend their time — right inside the editor.

## Key Features

- **Natural-language codebase Q&A** — ask architecture, dependency, and API questions in plain English and get answers grounded in your actual code.
- **Dependency & call-graph analysis** — instantly see which services, modules, or functions call a given API or function.
- **Change-impact analysis** — understand what might break *before* you modify a function.
- **Bug-history lookup** — surface related past issues for any module or function via Jira integration.
- **Auto-generated dependency graphs** — visualize how services and modules connect to one another.
- **Plain-English explanations** — get a clear summary of what a module, class, or function does.
- **Deep code understanding** — powered by AST parsing and a knowledge graph, not just keyword or embedding search.
- **Git-aware** — understands commit history and blame context, not just the current snapshot of the code.
- **Native VS Code extension** — ask questions without ever leaving your editor.
