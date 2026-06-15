"""Unit tests for the Zigbee2MQTT → home/{slug}/... bridge translation."""

import hub.edge.zigbee.bridge as bridge
from hub.edge.zigbee.bridge import translate


def setup_function():
    # Presence edge-detection state is module-global; reset between tests.
    bridge._presence_state.clear()


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
    assert set(out) == {"sensors", "alert"}
    assert out["sensors"]["tier"] == 1
    assert out["sensors"]["temperature"] == 22.9
    assert out["sensors"]["illuminance"] == 1514
    assert out["alert"]["tier"] == 2
    assert out["alert"]["alert_type"] == "motion"


def test_presence_is_edge_triggered():
    # presence=false → climate only, no alert.
    first = _by_subtopic(translate("vitalnia", "motion", {"presence": False, "temperature": 22.0}))
    assert "alert" not in first
    # rising edge → alert emitted.
    rise = _by_subtopic(translate("vitalnia", "motion", {"presence": True, "temperature": 22.0}))
    assert "alert" in rise
    # held high → no repeat alert (only climate keeps flowing).
    held = _by_subtopic(translate("vitalnia", "motion", {"presence": True, "temperature": 22.0}))
    assert "alert" not in held


def test_occupancy_field_also_accepted():
    out = _by_subtopic(translate("kitchen", "motion", {"occupancy": True}))
    assert out["alert"]["alert_type"] == "motion"


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
