"""
config.py — Shared configuration and helpers for the CodeMind (Argus) AI engine.

Centralizes constants, ignore lists, language mappings, and client factories
so that indexer.py, search.py, and rag_pipeline.py stay DRY.
"""

import glob
import os
import re
import sys
from pathlib import Path
import threading
import uuid
from functools import lru_cache

from dotenv import load_dotenv
from langchain_text_splitters import Language
from qdrant_client import QdrantClient
from fastembed import TextEmbedding

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from knowledge_base.identity import repository_id

load_dotenv()   # every entry point (api, indexer, search, rag) now sees QDRANT_API_KEY etc.

# ──────────────────────────────────────────────
# Local FAISS / optional remote Qdrant settings
# ──────────────────────────────────────────────
COLLECTION_NAME = "codemind_codebase"
VECTOR_SIZE = 384  # BAAI/bge-small-en-v1.5 output dimension
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
QDRANT_HOST = "127.0.0.1"
QDRANT_PORT = 6333

# ──────────────────────────────────────────────
# Data directory (stores index state & graph outside target repos)
# ──────────────────────────────────────────────
DEFAULT_DATA_DIR = os.path.expanduser(os.environ.get("CODEMIND_DATA_DIR", "~/.codemind/data"))

# ──────────────────────────────────────────────
# Indexer defaults
# ──────────────────────────────────────────────
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150
DEFAULT_TOP_K = 5
MAX_FILE_BYTES = 1_000_000
EMBED_BATCH = 64
INDEX_STATE_FILENAME = "index_state.json"
GRAPH_FILENAME = "graph.json"
SESSION_TOKEN_FILENAME = "session_token"
REPO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

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
    "out",
    "target",
    "vendor",
    "coverage",
    ".next",
    ".gradle",
    ".vscode-test",
    ".eggs",
    ".idea",
    ".vscode",
    ".vs",
}

# ──────────────────────────────────────────────
# File extension → LangChain Language mapping
# ──────────────────────────────────────────────
CODE_EXTENSIONS: dict[str, Language] = {
    ".json": Language.JS,
    ".yaml": Language.MARKDOWN,
    ".yml": Language.MARKDOWN,
    ".toml": Language.MARKDOWN,
    ".ini": Language.MARKDOWN,
    ".py": Language.PYTHON,
    ".js": Language.JS,
    ".cjs": Language.JS,
    ".mjs": Language.JS,
    ".ts": Language.TS,
    ".mts": Language.TS,
    ".cts": Language.TS,
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
    _, ext = os.path.splitext(file_path)
    return CODE_EXTENSIONS.get(ext.lower())


def get_index_language(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix in ('.json','.yaml','.yml','.toml','.ini'):
        return {'.yml':'yaml'}.get(suffix,suffix[1:])
    language = get_language_for_file(file_path)
    return {'js':'javascript','ts':'typescript'}.get(language.value,language.value) if language else 'text'


# ──────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────

def ensure_private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def make_repo_id(target_dir: str) -> str:
    """Stable id shared by CLI and API (realpath + case-normalised for Windows drive letters)."""
    return repository_id(Path(target_dir).expanduser())


_VECTOR_LOCK = threading.RLock()
_VECTOR_CLIENT = None


class _LocalVectorClient:
    """Serialize embedded Qdrant operations across FastAPI worker threads."""
    def __init__(self, client):
        self.client = client
        self.is_embedded = True

    def __getattr__(self, name):
        member = getattr(self.client, name)
        if not callable(member):
            return member
        def call(*args, **kwargs):
            with _VECTOR_LOCK:
                return member(*args, **kwargs)
        return call


def get_qdrant_client():
    """Compatibility factory name: local FAISS unless QDRANT_URL is explicit."""
    global _VECTOR_CLIENT
    with _VECTOR_LOCK:
        if _VECTOR_CLIENT is None:
            _VECTOR_CLIENT = _create_vector_client()
        return _VECTOR_CLIENT


def _create_vector_client():
    api_key = os.environ.get("QDRANT_API_KEY") or None
    url = os.environ.get("QDRANT_URL")
    if url:
        return QdrantClient(url=url, api_key=api_key, timeout=30)
    from knowledge_base.vector_store import FaissClient
    # Keep the previous Qdrant directory untouched; rebuild into a new local store.
    return FaissClient(ensure_private_dir(os.path.join(DEFAULT_DATA_DIR, "faiss")))


@lru_cache(maxsize=1)
def get_embedding_model() -> TextEmbedding:
    cache_dir = ensure_private_dir(os.path.join(DEFAULT_DATA_DIR, "models"))
    return TextEmbedding(model_name=EMBEDDING_MODEL_NAME, cache_dir=cache_dir)


_EMBED_LOCK = threading.Lock()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Serialised, batched embedding. All callers use this instead of model.embed()."""
    if not texts:
        return []
    model = get_embedding_model()
    with _EMBED_LOCK:   # the API calls the shared model from several threadpool workers
        return [v.tolist() for v in model.embed(texts, batch_size=EMBED_BATCH)]


def atomic_write_text(path: str, text: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize_text(text: str) -> str:
    """Strip ESC/C0/C1 control characters (keeps newline and tab) from untrusted text before printing, logging or prompting."""
    return _CONTROL_CHARS.sub("", text)


def latest_repo_id() -> str | None:
    states = glob.glob(os.path.join(DEFAULT_DATA_DIR, "*", INDEX_STATE_FILENAME))
    return os.path.basename(os.path.dirname(max(states, key=os.path.getmtime))) if states else None


def find_graph_file(repo_id: str | None = None) -> str | None:
    repo_id = repo_id or latest_repo_id()
    if not repo_id or not REPO_ID_RE.fullmatch(repo_id):
        return None
    candidate = os.path.join(DEFAULT_DATA_DIR, repo_id, GRAPH_FILENAME)
    return candidate if os.path.isfile(candidate) else None
