#!/usr/bin/env bash
# Demo on/off switch. Usage: infra/modal/demo.sh on [--no-gpu] [--check] | off | status
# See infra/modal/demo.py for what each command does.
set -euo pipefail
exec uv run --no-project --with "modal>=1.6" python "$(dirname "$0")/demo.py" "$@"
