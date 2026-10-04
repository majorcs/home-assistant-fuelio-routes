#!/usr/bin/env bash
# Local Home Assistant for trying the integration: http://localhost:8124
#   ./start_e2e_homeassistant.sh            start in the foreground
#   ./start_e2e_homeassistant.sh --restart  stop a running instance first
#   ./start_e2e_homeassistant.sh --stop     stop a running instance
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
E2E_VENV="$ROOT_DIR/.venv-e2e-ha"
HA_CONFIG_DIR="$ROOT_DIR/.e2e-homeassistant"
HASS_BIN="$E2E_VENV/bin/hass"
PORT=8124

running_pids() {
  pgrep -f "$HASS_BIN -c $HA_CONFIG_DIR" || true
}

stop_running() {
  local pids
  pids="$(running_pids)"
  [[ -z "$pids" ]] && return 0
  # shellcheck disable=SC2086
  kill $pids
  for _ in {1..30}; do
    [[ -z "$(running_pids)" ]] && return 0
    sleep 1
  done
  echo "Timed out waiting for Home Assistant to stop." >&2
  return 1
}

case "${1:-}" in
  --stop)
    stop_running
    exit 0
    ;;
  --restart)
    stop_running
    ;;
  "")
    if [[ -n "$(running_pids)" ]]; then
      echo "Already running at http://localhost:$PORT (use --restart or --stop)."
      exit 0
    fi
    ;;
  *)
    echo "Unknown option: $1" >&2
    exit 2
    ;;
esac

if [[ ! -x "$HASS_BIN" ]]; then
  python3 -m venv "$E2E_VENV"
  "$E2E_VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$E2E_VENV/bin/python" -m pip install homeassistant
fi

mkdir -p "$HA_CONFIG_DIR/custom_components"
ln -sfn "$ROOT_DIR/custom_components/fuelio_routes" "$HA_CONFIG_DIR/custom_components/fuelio_routes"

if [[ ! -f "$HA_CONFIG_DIR/configuration.yaml" ]]; then
  # "my" is left out on purpose: without it the Google OAuth redirect goes straight
  # back to http://localhost:$PORT instead of the instance linked at my.home-assistant.io.
  cat > "$HA_CONFIG_DIR/configuration.yaml" <<EOF
homeassistant:
  name: Fuelio Routes test
  latitude: 47.4979
  longitude: 19.0402
  time_zone: Europe/Budapest
  unit_system: metric

http:
  server_host: 127.0.0.1
  server_port: $PORT

frontend:
config:
application_credentials:
history:
logbook:

logger:
  default: warning
  logs:
    custom_components.fuelio_routes: debug
EOF
fi

exec "$HASS_BIN" -c "$HA_CONFIG_DIR"
