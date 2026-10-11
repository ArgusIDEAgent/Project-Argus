"""Start the engine with the project's virtual environment, from any directory."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not python.is_file():
    raise SystemExit("Create the environment first: python3 -m venv .venv\nThen: .venv/bin/python -m pip install -r requirements.txt")
check = subprocess.run([str(python), "-c", "import fastapi, fastembed, qdrant_client, tree_sitter"], capture_output=True)
if check.returncode:
    raise SystemExit("Missing engine dependencies. Run: .venv/bin/python -m pip install -r requirements.txt")
os.chdir(root / "ai-engine")
os.execv(str(python), [str(python), "api.py"])
