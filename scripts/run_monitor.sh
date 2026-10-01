#!/bin/sh
# Intended scheduler entry point. Builds local previews; never sends email.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
exec "$project_dir/.venv/bin/python" -m citywatch scan "$@"
