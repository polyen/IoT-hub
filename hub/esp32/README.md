# ESP32 Sensor Node

Room sensor node running ESPHome. Hardware: ESP32 + DHT22 + MQ-2 + PIR + relay.

## Prerequisites

```bash
pip install esphome
```

## Secrets setup

Copy the example file and fill in your credentials:

```bash
cp secrets.yaml.example secrets.yaml
```

Edit `secrets.yaml`:
- `wifi_ssid` / `wifi_password` — your WiFi network
- `mqtt_broker` — IP address of your Mosquitto broker (e.g. `192.168.1.100`)
- `mqtt_username` / `mqtt_password` — MQTT credentials from `hub/mosquitto/acl.conf`

> `secrets.yaml` is git-ignored. Never commit it.

## Change room name

Edit the substitution at the top of `sensor-node.yaml`:

```yaml
substitutions:
  room_name: "bedroom"   # change to: living_room, kitchen, bedroom, etc.
```

The room name propagates to MQTT topics, device name, and client ID automatically.

## Flash

Connect ESP32 via USB, then:

```bash
esphome run sensor-node.yaml
```

For OTA updates (after first flash):

```bash
esphome run sensor-node.yaml --device <device-ip>
```

## MQTT topic format

| Topic | Direction | Interval | Description |
|---|---|---|---|
| `home/{room}/sensors` | publish | 10 s | Full sensor snapshot |
| `home/{room}/alert` | publish | on trigger | Motion / gas alert |
| `home/{room}/cmd` | subscribe | — | Relay command (future) |

### Sensor payload (`home/{room}/sensors`)

```json
{
  "room": "living_room",
  "temperature": 22.5,
  "humidity": 55.0,
  "gas_ppm": 210.3,
  "pir": false,
  "tier": 1
}
```

### Alert payload (`home/{room}/alert`)

```json
{
  "room": "living_room",
  "event": "motion_detected",
  "tier": 1
}
```

Gas alert adds `"gas_ppm"` field.

## Offline buffering

The node buffers up to 100 sensor messages in RAM while MQTT is disconnected and replays them on reconnect. Buffer is not persisted across power cycles.
