# CodeMind (Argus)

**An AI-powered engineering assistant that understands your organization's own codebase — not just code in general.**

> **Status: Early MVP.** This README describes what's actually built today and how it maps to the long-term vision below. For the full phased build-out plan, see the [Roadmap](#roadmap) below.

## Table of Contents
- [Project Vision](#project-vision)
- [Current State (MVP)](#current-state-mvp)
- [Feature Status: Implemented vs. Planned](#feature-status-implemented-vs-planned)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)

## Project Vision

Onboarding a new developer into a large, mature codebase is one of the most expensive and least enjoyable parts of software engineering. New hires don't yet know the architecture, the dependency graph, the internal APIs, the team's coding standards, or the history of past bugs — so senior engineers end up re-explaining the same things over and over.

CodeMind (Argus) exists to fix that by actually understanding *your* codebase, not code in general. The long-term vision combines:

- **Retrieval-Augmented Generation (RAG)** over a vector database, for natural-language Q&A grounded in real code
- **AST-based static analysis** and a **knowledge graph** of the codebase's structure, for questions that require real understanding rather than text similarity
- **Git integration**, for commit history and blame-aware context
- **Jira integration**, for surfacing related bug history
- A **native VS Code extension**, so this knowledge lives exactly where developers already work

The end goal is a tool developers can ask things like *"Where is JWT authentication implemented?"*, *"Which services call this API?"*, *"What happens if I modify this function?"*, or *"Show me related bugs"* — and get answers grounded in the actual, current state of the codebase.

## Current State (MVP)

Today, CodeMind is a **local, single-file RAG pipeline** — the foundation the rest of the vision will be built on top of. Concretely, the current implementation can:

1. Split a source file into chunks using a code-aware text splitter
2. Generate embeddings for those chunks **locally**, with no external embedding API
3. Store the vectors in a local Qdrant collection
4. Turn a natural-language question into a vector, retrieve the closest matching code chunk, and pass it to an LLM to generate a grounded answer

**MVP constraints worth being upfront about:**
- Generation uses a free-tier OpenRouter model, which is great for $0 experimentation but not yet meant for reliability-sensitive use.
- None of the AST parsing, knowledge graph, Git awareness, Jira integration, or VS Code extension described in the vision exist yet — the MVP is semantic retrieval over raw text chunks, and nothing more.

## Feature Status: Implemented vs. Planned

| Feature | Status | Notes |
|---|---|---|
| Natural-language codebase Q&A | ✅ MVP | Single-file RAG via Qdrant + LiteLLM |
| Plain-English explanations | ✅ MVP (partial) | LLM summarizes whatever chunk is retrieved |
| Semantic code search | ✅ MVP | `search.py` |
| Dependency & call-graph analysis | 🔜 Phase 2 | Needs AST parsing + a knowledge graph |
| Change-impact analysis | 🔜 Phase 2 | Needs call-graph traversal |
| Auto-generated dependency graphs | 🔜 Phase 2 | |
| Git-aware / blame context | 🔜 Phase 3 | |
| Bug-history lookup (Jira) | 🔜 Phase 3 | |
| Native VS Code extension | 🔜 Phase 4 | |

## Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| Vector database | [Qdrant](https://qdrant.tech/) | Local, self-hosted storage and similarity search for code embeddings |
| Embeddings | [FastEmbed](https://github.com/qdrant/fastembed) (`BAAI/bge-small-en-v1.5`) | Local, CPU-friendly 384-dim embedding generation — no external embedding API cost |
| Chunking | [LangChain Text Splitters](https://python.langchain.com/) (`RecursiveCharacterTextSplitter`, `Language.PYTHON`) | Code-aware splitting that respects Python syntax boundaries |
| LLM orchestration | [LiteLLM](https://www.litellm.ai/) | Unified interface for calling LLMs across providers; currently routes to OpenRouter |
| LLM provider (default) | OpenRouter → Cohere (`north-mini-code:free`) | Free-tier generation model used by the MVP |
| Config | `python-dotenv` | Loads API keys/config from a local `.env` file |

## Getting Started

### Prerequisites
- Python 3.10+
- Docker (recommended, for running Qdrant locally) — or a local Qdrant binary
- A free [OpenRouter](https://openrouter.ai/) API key

### 1. Clone and install dependencies
```bash
git clone <repo-url>
cd argusideagent-project-argus
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start Qdrant
Qdrant is bound to loopback and protected by an API key, so no one else on the
network can read your indexed code or inject poisoned points:
```bash
export QDRANT_API_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
docker run -d --name qdrant \
  -p 127.0.0.1:6333:6333 -p 127.0.0.1:6334:6334 \
  -e QDRANT__SERVICE__API_KEY="$QDRANT_API_KEY" \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```
Add the same value as `QDRANT_API_KEY=` in `ai-engine/.env` so the engine can
authenticate. The named volume keeps the vectors across container restarts.

### 3. Set environment variables
Create a `.env` file inside `ai-engine/` (see `ai-engine/.env.example`):
```env
OPENROUTER_API_KEY=your_key_here
QDRANT_API_KEY=your_qdrant_api_key_here
```
LiteLLM picks this up automatically when routing to `openrouter/...` models.

### 4. Run the scripts
```bash
cd ai-engine
python indexer.py --dir ./sample_code          # add --full-reindex to rebuild from scratch
python search.py --query "How does authentication work?"
python rag_pipeline.py --query "Where is JWT authentication implemented?"
python graph_query.py --stats
python api.py                                  # engine for the VS Code extension (127.0.0.1:8000)
```

## Project Structure
```
argusideagent-project-argus/
├── README.md
├── project_plan.md
├── requirements.txt
└── ai-engine/
    ├── api.py             # FastAPI engine for the VS Code extension
    ├── config.py          # Shared settings, Qdrant/FastEmbed clients, helpers
    ├── indexer.py         # Walk → chunk → embed → store in Qdrant
    ├── graph_builder.py   # AST → NetworkX knowledge graph
    ├── graph_query.py     # CLI for structural graph queries
    ├── rag_pipeline.py    # Retrieve → augment → generate (full RAG)
    ├── search.py          # Retrieval-only sanity check
    ├── test_ai.py         # LLM connectivity check
    └── sample_code/
        └── auth_service.py
```

## Roadmap

The path from this MVP to the full vision is laid out in four phases below:

1. **MVP Hardening** — full-repo indexing, incremental updates, better retrieval, config/CLI/testing
2. **Advanced Code Understanding** — AST parsing + a knowledge graph for call/dependency analysis
3. **External Integrations** — Git history and Jira bug-history linkage
4. **Developer Experience** — a backend API and a native VS Code extension