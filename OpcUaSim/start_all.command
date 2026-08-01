#!/bin/bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
. "$PROJECT_ROOT/scripts/unix_common.sh"

SERVER_PID=""
AGENT_PID=""

opcuasim_stop_child() {
    local pid="$1"

    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
        wait "$pid" >/dev/null 2>&1 || true
    fi
}

opcuasim_all_exit() {
    local status=$?
    trap - EXIT INT TERM
    opcuasim_stop_child "$AGENT_PID"
    opcuasim_stop_child "$SERVER_PID"
    opcuasim_report_exit "$status"
    exit "$status"
}
trap opcuasim_all_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

opcuasim_ensure_venv "$PROJECT_ROOT"
cd "$PROJECT_ROOT"

LISTEN_HOST="${OPCUASIM_HOST:-0.0.0.0}"
CLIENT_HOST="${OPCUASIM_CLIENT_HOST:-127.0.0.1}"
PORT="${OPCUASIM_PORT:-4855}"

case "$PORT" in
    ''|*[!0-9]*)
        opcuasim_log "[X] OPCUASIM_PORT 必须是数字，当前值: $PORT"
        exit 2
        ;;
esac

CSV_ARGS=()
for arg in "$@"; do
    case "$arg" in
        *.[cC][sS][vV]) CSV_ARGS+=(--csv "$arg") ;;
        *)
            opcuasim_log "[X] start_all.command 只接受 CSV 文件参数: $arg"
            opcuasim_log "    其他 CLI 参数请使用 start.command 和 start_handshake.command。"
            exit 2
            ;;
    esac
done

ENDPOINT="opc.tcp://$CLIENT_HOST:$PORT/xuse_sim/"

printf '\n==============================================================\n'
printf '  OpcUaSim Server + XUSE Handshake Agent\n'
printf '  Endpoint: %s\n' "$ENDPOINT"
printf '  按 Ctrl+C 同时停止两个进程。\n'
printf '==============================================================\n\n'

"$OPCUASIM_PY" "$PROJECT_ROOT/server.py" \
    --host "$LISTEN_HOST" --port "$PORT" "${CSV_ARGS[@]}" &
SERVER_PID=$!

opcuasim_log "正在等待 OPC UA Server 就绪..."
if ! opcuasim_wait_for_tcp "$CLIENT_HOST" "$PORT" 150; then
    opcuasim_log "[X] 15 秒内未检测到 Server 端口。"
    exit 1
fi
if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    opcuasim_log "[X] OPC UA Server 已提前退出。"
    exit 1
fi

opcuasim_log "Server 已就绪，启动 Handshake Agent..."
"$OPCUASIM_PY" "$PROJECT_ROOT/handshake_agent.py" \
    --url "$ENDPOINT" "${CSV_ARGS[@]}" &
AGENT_PID=$!

while kill -0 "$SERVER_PID" >/dev/null 2>&1 \
    && kill -0 "$AGENT_PID" >/dev/null 2>&1; do
    sleep 1
done

if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    if wait "$SERVER_PID"; then
        exit 0
    else
        exit $?
    fi
fi

if wait "$AGENT_PID"; then
    exit 0
else
    exit $?
fi
