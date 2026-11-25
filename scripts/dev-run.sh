#!/bin/bash
# Arranca el motor en primer plano (desarrollo). Uso: scripts/dev-run.sh
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python -m tidalconnect
