# CodeMind (Argus)

**An AI-powered engineering assistant that understands your organization's own codebase — not just code in general.**

> **Status: Early MVP.** This README describes what's actually built today and how it maps to the long-term vision below. For the full phased build-out plan, see [`PROJECT_PLAN.md`](./PROJECT_PLAN.md).

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
- The indexer currently points at a single hardcoded file (`sample_code/auth_service.py`), not a full repository — walking an entire directory tree is a Phase 1 item.
- Retrieval always returns just the single closest chunk (`limit=1`); there's no re-ranking or multi-chunk context yet.
- There's no incremental indexing — re-running `indexer.py` re-embeds everything from scratch.
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
```bash
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
```
Confirm it's running by visiting `http://localhost:6333/dashboard`.

### 3. Set environment variables
Create a `.env` file inside `ai-engine/`:
```env
OPENROUTER_API_KEY=your_key_here
```
LiteLLM picks this up automatically when routing to `openrouter/...` models.

### 4. Run the scripts — in this order

| Step | Command | What it does |
|---|---|---|
| 1 | `cd ai-engine` | Move into the engine directory |
| 2 | `python indexer.py` | Chunks `sample_code/auth_service.py`, embeds it, and pushes vectors into the `codemind_codebase` Qdrant collection. **Must run first**, and must be re-run whenever the source file changes. |
| 3 | `python search.py` | Retrieval-only sanity check — runs a sample semantic search query, no LLM call |
| 4 | `python rag_pipeline.py` | Full RAG: retrieves the relevant chunk and asks the LLM a question grounded in it |
| Optional | `python test_ai.py` | Standalone check that your LiteLLM/OpenRouter connection is working |

> ⚠️ `indexer.py` must complete successfully before `search.py` or `rag_pipeline.py` — both query a Qdrant collection that only exists after indexing has run.

## Project Structure
```
argusideagent-project-argus/
├── README.md
├── PROJECT_PLAN.md
├── requirements.txt
└── ai-engine/
    ├── indexer.py         # Chunk → embed → store in Qdrant
    ├── rag_pipeline.py    # Retrieve → augment → generate (full RAG)
    ├── search.py          # Retrieval-only sanity check
    ├── test_ai.py         # LLM connectivity check
    └── sample_code/
        └── auth_service.py
```

## Roadmap

The path from this MVP to the full vision is laid out in four phases in [`PROJECT_PLAN.md`](./PROJECT_PLAN.md):

1. **MVP Hardening** — full-repo indexing, incremental updates, better retrieval, config/CLI/testing
2. **Advanced Code Understanding** — AST parsing + a knowledge graph for call/dependency analysis
3. **External Integrations** — Git history and Jira bug-history linkage
4. **Developer Experience** — a backend API and a native VS Code extension