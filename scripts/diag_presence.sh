#!/usr/bin/env bash
# Diagnose the Zigbee presence → floor-plan glow chain, one link at a time.
#
# The events-feed "Рух" and the floor-plan glow are SEPARATE chains: the feed
# needs no placement (presence event is keyed by room slug), but the glow needs
#   bridge level /state  →  Redis home:state:{device_id}  →  a DevicePlacement
#   with that exact device_id  →  room_states.
#
# Usage:  scripts/diag_presence.sh <room_slug> <kind>
#   e.g.  scripts/diag_presence.sh spalnia presence
# Env:    API_BASE (default https://iot-hub.local), COMPOSE_FILE
set -uo pipefail

SLUG="${1:?room slug (Z2M friendly name is "{slug}/{kind}"), e.g. spalnia}"
KIND="${2:?device kind (2nd segment of the friendly name), e.g. presence}"
API="${API_BASE:-https://iot-hub.local}"
CF="${COMPOSE_FILE:-hub/docker-compose.edge.yml}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE="docker compose --env-file $ENV_FILE -f $CF"
DEVID="zigbee-${SLUG}-${KIND}"

echo "Expecting device_id = $DEVID"
echo "Friendly name in Z2M must be exactly: ${SLUG}/${KIND}"
echo "──────────────────────────────────────────────────────────────"

# Preflight: compose must be able to interpolate the stack (loads $ENV_FILE).
# Without --env-file this fails on :?required vars and would silently produce
# false negatives below.
if ! cfg_err="$($COMPOSE config --quiet 2>&1)"; then
  echo "✗ docker compose can't parse the stack — env not loaded?"
  echo "$cfg_err" | sed 's/^/    /'
  echo "    → run from the repo root; ensure $ENV_FILE exists (or ENV_FILE=/path ./scripts/diag_presence.sh …)"
  exit 1
fi

echo "[1/3] Bridge level state — listening 10s on home/${SLUG}/${KIND}/state."
echo "      >>> Trigger the sensor now (walk in front of it) <<<"
state_out="$($COMPOSE exec -T mosquitto mosquitto_sub -t "home/${SLUG}/${KIND}/state" -W 10 -v 2>/dev/null || true)"
if [ -z "$state_out" ]; then
  echo "  ✗ No /state messages. The bridge isn't emitting the level state."
  echo "    → Is the bridge redeployed?  $COMPOSE up -d --build zigbee-bridge"
  echo "    → Is the presence field a real bool? check:  $COMPOSE exec -T mosquitto mosquitto_sub -t 'zigbee2mqtt/${SLUG}/${KIND}' -W 10 -v"
else
  echo "  ✓ state seen:"; echo "$state_out" | sed 's/^/      /'
fi
echo

echo "[2/3] Redis home:state:${DEVID}"
redis_out="$($COMPOSE exec -T redis redis-cli hgetall "home:state:${DEVID}" 2>/dev/null || true)"
if [ -z "$redis_out" ]; then
  echo "  ✗ Empty. The state topic isn't being written to Redis."
  echo "    → Is the backend redeployed (it needs the _handle_device_state device_id"
  echo "      fallback for read-only devices)?  $COMPOSE up -d --build backend"
else
  echo "$redis_out" | sed 's/^/      /'
  echo "$redis_out" | grep -qx "true" && echo "  ✓ presence=true present" || echo "  ⚠ presence not 'true' right now (move and re-run, or it cleared)"
fi
echo

echo "[3/3] DevicePlacement + room_states"
placement="$(curl -sk "$API/api/floorplan" | jq -r --arg d "$DEVID" '.placements[] | select(.device_id==$d) | "\(.device_id)  room_id=\(.room_id)  kind=\(.kind)"')"
if [ -z "$placement" ]; then
  echo "  ✗ No placement with device_id=$DEVID."
  echo "    → THIS is usually why the feed works but the glow doesn't."
  echo "    → Add it: Дім → Редагувати (or /more/devices), device_id=$DEVID,"
  echo "      controllable=false, in room '${SLUG}'."
else
  echo "  ✓ placement: $placement"
fi
echo "  room_states now:"
curl -sk "$API/api/floorplan/room_states" | jq -c . | sed 's/^/      /'
echo "──────────────────────────────────────────────────────────────"
echo "Glow shows when room_states.presence_rooms contains the room id above"
echo "AND Redis presence=true (front-end polls every 30s)."
