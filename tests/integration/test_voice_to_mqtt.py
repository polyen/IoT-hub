"""Integration test for the full voice → MQTT command pipeline.

Exercises the path:
    voice text → AgentOrchestrator.handle_command → IntentRouter (keyword fallback)
    → TextResolver → DeviceRegistry.find → mqtt_publish (real aiomqtt mock)

The DeviceRegistry is wired with a single controllable light in room "vitalnya".
A real PostgreSQL is *not* required — the registry is constructed with an
``AsyncMock`` session factory and its in-memory ``_devices`` list is seeded
directly so the resolver sees a realistic ResolvedDevice.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.backend.services.device_registry import DeviceRegistry, ResolvedDevice
from hub.edge.agent.orchestrator import AgentOrchestrator
from hub.edge.agent.policy import ActionClass, Decision, PolicyEngine
from hub.edge.agent.router import Intent, IntentClass, IntentRouter

pytestmark = pytest.mark.integration


def _make_registry_with_light() -> DeviceRegistry:
    """Return a DeviceRegistry preloaded with a single controllable light."""
    registry = DeviceRegistry(
        session_factory=AsyncMock(),
        redis_client=AsyncMock(),
    )
    import uuid

    registry._devices = [  # type: ignore[attr-defined]
        ResolvedDevice(
            placement_id=uuid.uuid4(),
            device_id="light-vitalnya-01",
            kind="light",
            label="Люстра",
            room_slug="vitalnya",
            room_name_ua="Вітальня",
            mqtt_command_topic="home/vitalnya/light/cmd",
            mqtt_state_topic=None,
            actions=["on", "off", "toggle"],
            payload_on={"state": "ON"},
            payload_off={"state": "OFF"},
            device_aliases=["люстра", "лампа"],
            room_aliases=["зала", "велика кімната"],
        )
    ]
    return registry


def _make_orchestrator(registry: DeviceRegistry) -> tuple[AgentOrchestrator, AsyncMock]:
    """Build an orchestrator wired exactly like main.py does in production."""
    router = MagicMock(spec=IntentRouter)
    router.classify_intent.return_value = Intent(
        class_=IntentClass.DETERMINISTIC, score=0.9, prototype="light_on"
    )

    policy = MagicMock(spec=PolicyEngine)
    policy.evaluate.return_value = Decision(action_class=ActionClass.AUTO, reason="ok")

    mqtt_client = AsyncMock()
    redis_client = AsyncMock()
    redis_client.lpush = AsyncMock()
    redis_client.ltrim = AsyncMock()
    redis_client.publish = AsyncMock()

    orch = AgentOrchestrator(
        policy=policy,
        router=router,
        llm=AsyncMock(),
        redis_client=redis_client,
        mqtt_client=mqtt_client,
        device_registry=registry,
    )
    return orch, mqtt_client


@pytest.mark.asyncio
async def test_voice_command_light_on_publishes_to_mqtt() -> None:
    """«увімкни світло у вітальні» → MQTT publish on home/vitalnya/light/cmd."""
    registry = _make_registry_with_light()
    orch, mqtt_client = _make_orchestrator(registry)

    with patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock):
        await orch.handle_command("увімкни світло у вітальні")

    mqtt_client.publish.assert_awaited_once()
    topic, payload = mqtt_client.publish.await_args[0]
    assert topic == "home/vitalnya/light/cmd"
    # payload may be bytes or str (json); decode if needed
    import json

    body = json.loads(payload if isinstance(payload, str | bytes) else payload)
    assert body == {"state": "ON"}


@pytest.mark.asyncio
async def test_voice_command_unknown_device_no_mqtt_publish() -> None:
    """«увімкни кондиціонер» (no AC registered) → no MQTT publish, INFO result."""
    registry = _make_registry_with_light()
    orch, mqtt_client = _make_orchestrator(registry)

    published: list[tuple[str, str]] = []

    async def _capture_publish(channel: str, payload: str) -> int:
        published.append((channel, payload))
        return 1

    orch._redis.publish = AsyncMock(side_effect=_capture_publish)

    with patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock):
        await orch.handle_command("увімкни кондиціонер")

    # No MQTT publish on the broker
    mqtt_client.publish.assert_not_awaited()

    # An INFO agent:result was published to Redis (the explainable-failure path)
    result_events = [payload for channel, payload in published if channel == "agent:result"]
    assert result_events, f"expected agent:result, got {published!r}"

    import json

    info_results = [json.loads(p) for p in result_events]
    assert any(r.get("action_class") == "INFO" for r in info_results), info_results


@pytest.mark.asyncio
async def test_voice_command_room_alias_resolves() -> None:
    """«увімкни світло у залі» (alias for Вітальня) → MQTT publish."""
    registry = _make_registry_with_light()
    orch, mqtt_client = _make_orchestrator(registry)

    with patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock):
        await orch.handle_command("увімкни світло у залі")

    mqtt_client.publish.assert_awaited_once()
    topic, _ = mqtt_client.publish.await_args[0]
    assert topic == "home/vitalnya/light/cmd"
