"""
config.py — Shared configuration and helpers for the CodeMind (Argus) AI engine.

Centralizes constants, ignore lists, language mappings, and client factories
so that indexer.py, search.py, and rag_pipeline.py stay DRY.
"""

from langchain_text_splitters import Language
from qdrant_client import QdrantClient
from fastembed import TextEmbedding

# ──────────────────────────────────────────────
# Qdrant / Vector settings
# ──────────────────────────────────────────────
COLLECTION_NAME = "codemind_codebase"
VECTOR_SIZE = 384  # BAAI/bge-small-en-v1.5 output dimension
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333

# ──────────────────────────────────────────────
# Indexer defaults
# ──────────────────────────────────────────────
DEFAULT_CHUNK_SIZE = 300
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_TOP_K = 5
INDEX_STATE_FILENAME = ".codemind_index_state.json"

# ──────────────────────────────────────────────
# Directories to skip during recursive walk
# ──────────────────────────────────────────────
IGNORE_DIRS: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "egg-info",
    ".eggs",
    ".idea",
    ".vscode",
    ".vs",
}

# ──────────────────────────────────────────────
# File extension → LangChain Language mapping
# ──────────────────────────────────────────────
CODE_EXTENSIONS: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JS,
    ".ts": Language.TS,
    ".jsx": Language.JS,
    ".tsx": Language.TS,
    ".java": Language.JAVA,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".rb": Language.RUBY,
    ".php": Language.PHP,
    ".scala": Language.SCALA,
    ".swift": Language.SWIFT,
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
    ".html": Language.HTML,
    ".htm": Language.HTML,
    ".c": Language.C,
    ".h": Language.C,
    ".cpp": Language.CPP,
    ".hpp": Language.CPP,
    ".cs": Language.CSHARP,
}


def get_language_for_file(file_path: str) -> Language | None:
    """Return the LangChain Language enum for a file, or None if unsupported."""
    import os
    _, ext = os.path.splitext(file_path)
    return CODE_EXTENSIONS.get(ext.lower())


def get_qdrant_client() -> QdrantClient:
    """Return a connected Qdrant client using the default host/port."""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


def get_embedding_model() -> TextEmbedding:
    """Return the FastEmbed embedding model (lazy singleton pattern)."""
    return TextEmbedding(model_name=EMBEDDING_MODEL_NAME)
