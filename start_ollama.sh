#!/usr/bin/env bash
# Start the user-local Ollama server that CodeMind uses for grounded answer prose.
#
# Installed without root (no sudo, no system package) under ~/.local/opt/ollama.
# Remove that directory to uninstall completely.
#
# Usage:  ./start_ollama.sh          # foreground (Ctrl-C to stop)
#         ./start_ollama.sh --check  # report status and exit
set -euo pipefail

OLLAMA_HOME="${HOME}/.local/opt/ollama"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/.ollama/models}"
export LD_LIBRARY_PATH="${OLLAMA_HOME}/lib:${LD_LIBRARY_PATH:-}"
# Hold the model in RAM for 30 minutes instead of the 5-minute default, so a pause
# mid-presentation does not cost a ~6.5 s reload (5.4 s of which is model load).
# This keeps ~3 GB resident, so close memory-hungry apps (a browser is typically
# 1.5 GB) before the demo. Measured on this host: unloaded = 3.9 GiB available,
# loaded = 1.7 GiB available.
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"

if [ ! -x "${OLLAMA_HOME}/bin/ollama" ]; then
  echo "Ollama is not installed at ${OLLAMA_HOME}." >&2
  echo "Install it with:" >&2
  echo "  mkdir -p ~/.local/opt && cd ~/.local/opt" >&2
  echo "  curl -L -o o.tar.zst https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst" >&2
  echo "  mkdir -p ollama && tar --zstd -xf o.tar.zst -C ollama" >&2
  exit 1
fi

if curl -fsS -m 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo "Ollama is already running: $(curl -s -m 2 http://127.0.0.1:11434/api/version)"
  if [ "${1:-}" = "--check" ]; then
    echo "Models installed:"
    "${OLLAMA_HOME}/bin/ollama" list
    exit 0
  fi
fi

if [ "${1:-}" = "--check" ]; then
  echo "Ollama is NOT running. Start it with: $0" >&2
  exit 1
fi

echo "Starting Ollama on 127.0.0.1:11434 (models in ${OLLAMA_MODELS})"
exec "${OLLAMA_HOME}/bin/ollama" serve
