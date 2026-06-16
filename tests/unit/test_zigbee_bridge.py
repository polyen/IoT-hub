"""Unit tests for the Zigbee2MQTT → home/{slug}/... bridge translation."""

import hub.edge.zigbee.bridge as bridge
from hub.edge.zigbee.bridge import translate


def setup_function():
    # Edge-detection state is module-global; reset between tests.
    bridge._edge_state.clear()


def _by_subtopic(results):
    return dict(results)


def test_climate_maps_to_sensors_tier1():
    out = _by_subtopic(
        translate("living_room", "temp", {"temperature": 21.5, "humidity": 47, "battery": 88})
    )
    assert "sensors" in out
    p = out["sensors"]
    assert p["tier"] == 1
    assert p["temperature"] == 21.5
    assert p["humidity"] == 47
    assert p["battery"] == 88  # diagnostics carried through
    assert p["device_id"] == "zigbee-living_room-temp"


def test_combo_sensor_fans_out_climate_and_motion():
    # mmWave presence sensor that also reports temp/humidity/illuminance, all in
    # one message. Climate must NOT be lost just because the device is named motion.
    out = _by_subtopic(
        translate(
            "vitalnia",
            "motion",
            {"presence": True, "temperature": 22.9, "humidity": 48, "illuminance": 1514},
        )
    )
    # Motion is routed to the dedicated `presence` topic (not `alert`).
    assert set(out) == {"sensors", "presence", "motion/state"}
    assert out["sensors"]["tier"] == 1
    assert out["sensors"]["temperature"] == 22.9
    assert out["sensors"]["illuminance"] == 1514
    assert out["presence"]["tier"] == 2
    assert out["presence"]["confidence"] == 1.0
    # Level presence state drives the floor-plan glow.
    assert out["motion/state"]["presence"] == "true"
    assert out["motion/state"]["device_id"] == "zigbee-vitalnia-motion"


def test_presence_is_edge_triggered():
    # presence=false → climate only, no presence event; state reports "false".
    first = _by_subtopic(translate("vitalnia", "motion", {"presence": False, "temperature": 22.0}))
    assert "presence" not in first
    assert first["motion/state"]["presence"] == "false"
    # rising edge → presence event emitted, state "true".
    rise = _by_subtopic(translate("vitalnia", "motion", {"presence": True, "temperature": 22.0}))
    assert "presence" in rise
    assert rise["motion/state"]["presence"] == "true"
    # held high → no repeat event, but state stays "true" (level, not edge).
    held = _by_subtopic(translate("vitalnia", "motion", {"presence": True, "temperature": 22.0}))
    assert "presence" not in held
    assert held["motion/state"]["presence"] == "true"


def test_presence_state_clears_on_falling_edge():
    # Occupied, then room clears: no new event on the way down, but the level
    # state flips to "false" so room_states stops lighting the room.
    translate("vitalnia", "motion", {"presence": True})
    cleared = _by_subtopic(translate("vitalnia", "motion", {"presence": False}))
    assert "presence" not in cleared
    assert cleared["motion/state"]["presence"] == "false"


def test_occupancy_field_also_accepted():
    out = _by_subtopic(translate("kitchen", "motion", {"occupancy": True}))
    assert out["presence"]["confidence"] == 1.0
    assert out["motion/state"]["presence"] == "true"


def test_water_leak_is_tier2_alert_edge_triggered():
    # Dry → no alert.
    assert "alert" not in _by_subtopic(translate("kitchen", "leak", {"water_leak": False}))
    # Dry → wet: critical alert.
    wet = _by_subtopic(translate("kitchen", "leak", {"water_leak": True, "battery": 95}))
    assert wet["alert"]["tier"] == 2
    assert wet["alert"]["alert_type"] == "water_leak"
    # Still wet → no repeat.
    assert "alert" not in _by_subtopic(translate("kitchen", "leak", {"water_leak": True}))


def test_contact_open_close_polarity():
    opened = _by_subtopic(translate("hall", "door", {"contact": False}))["alert"]
    closed = _by_subtopic(translate("hall", "door", {"contact": True}))["alert"]
    assert opened["alert_type"] == "door_open"
    assert closed["alert_type"] == "door_close"
    assert opened["tier"] == 2


def test_plug_power_maps_to_sensors_tier0():
    out = _by_subtopic(
        translate(
            "kitchen", "plug", {"power": 1850.0, "voltage": 229.1, "current": 8.07, "state": "ON"}
        )
    )["sensors"]
    assert out["tier"] == 0
    assert out["power_w"] == 1850.0
    assert out["voltage_v"] == 229.1
    assert out["state"] == "ON"


def test_unknown_kind_passes_numeric_fields():
    out = _by_subtopic(translate("bedroom", "widget", {"some_value": 3, "linkquality": 80}))[
        "sensors"
    ]
    assert out["tier"] == 1
    assert out["some_value"] == 3


def test_button_press_without_numeric_fields_drops():
    # A button press ("action": "single") carries no numeric state → nothing to persist.
    assert translate("bedroom", "button", {"action": "single"}) == []
