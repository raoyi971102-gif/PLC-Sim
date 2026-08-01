#!/bin/bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
. "$PROJECT_ROOT/scripts/unix_common.sh"

opcuasim_server_exit() {
    local status=$?
    trap - EXIT
    opcuasim_report_exit "$status"
    exit "$status"
}
trap opcuasim_server_exit EXIT

opcuasim_ensure_venv "$PROJECT_ROOT"
cd "$PROJECT_ROOT"

CSV_ARGS=()
EXTRA_ARGS=()
for arg in "$@"; do
    case "$arg" in
        *.[cC][sS][vV]) CSV_ARGS+=(--csv "$arg") ;;
        *) EXTRA_ARGS+=("$arg") ;;
    esac
done

printf '\n==============================================================\n'
printf '  OpcUaSim OPC UA Server\n'
printf '  Endpoint: opc.tcp://0.0.0.0:4855/xuse_sim/\n'
printf '  按 Ctrl+C 停止。\n'
printf '==============================================================\n\n'

"$OPCUASIM_PY" "$PROJECT_ROOT/server.py" "${CSV_ARGS[@]}" "${EXTRA_ARGS[@]}"
