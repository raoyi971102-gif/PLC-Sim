#!/bin/bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
. "$PROJECT_ROOT/scripts/unix_common.sh"

opcuasim_szlab_exit() {
    local status=$?
    trap - EXIT
    opcuasim_report_exit "$status"
    exit "$status"
}
trap opcuasim_szlab_exit EXIT

opcuasim_ensure_venv "$PROJECT_ROOT"
cd "$PROJECT_ROOT"

printf '\n==============================================================\n'
printf '  SZLab Poly Studio Handshake Simulator\n'
printf '  Target: opc.tcp://127.0.0.1:4855/xuse_sim/\n'
printf '  按 Ctrl+C 停止。\n'
printf '==============================================================\n\n'

"$OPCUASIM_PY" "$PROJECT_ROOT/szlab_handshake_agent.py" "$@"
