"""Unit tests for the Zigbee2MQTT → home/{slug}/... bridge translation."""

from hub.edge.zigbee.bridge import translate


def test_climate_maps_to_sensors_tier1():
    sub, payload = translate(
        "living_room", "temp", {"temperature": 21.5, "humidity": 47, "battery": 88}
    )
    assert sub == "sensors"
    assert payload["tier"] == 1
    assert payload["temperature"] == 21.5
    assert payload["humidity"] == 47
    assert payload["battery"] == 88  # diagnostics carried through
    assert payload["device_id"] == "zigbee-living_room-temp"


def test_motion_detected_emits_alert():
    sub, payload = translate("kitchen", "motion", {"occupancy": True})
    assert sub == "alert"
    assert payload["tier"] == 2
    assert payload["alert_type"] == "motion"


def test_motion_cleared_is_dropped():
    assert translate("kitchen", "motion", {"occupancy": False}) is None
    assert translate("kitchen", "motion", {}) is None


def test_contact_open_close_polarity():
    # Zigbee: contact=false means magnets separated → door open.
    _, opened = translate("hall", "door", {"contact": False})
    _, closed = translate("hall", "door", {"contact": True})
    assert opened["alert_type"] == "door_open"
    assert closed["alert_type"] == "door_close"
    assert opened["tier"] == 2


def test_plug_power_maps_to_sensors_tier0():
    sub, payload = translate(
        "kitchen", "plug", {"power": 1850.0, "voltage": 229.1, "current": 8.07, "state": "ON"}
    )
    assert sub == "sensors"
    assert payload["tier"] == 0
    assert payload["power_w"] == 1850.0
    assert payload["voltage_v"] == 229.1
    assert payload["state"] == "ON"


def test_unknown_kind_passes_numeric_fields():
    sub, payload = translate("bedroom", "widget", {"some_value": 3, "linkquality": 80})
    assert sub == "sensors"
    assert payload["tier"] == 1
    assert payload["some_value"] == 3


def test_unknown_kind_without_numeric_fields_dropped():
    # A button press ("action": "single") carries no numeric state → nothing to persist.
    assert translate("bedroom", "button", {"action": "single"}) is None
