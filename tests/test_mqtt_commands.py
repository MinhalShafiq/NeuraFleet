"""Gateway CommandBus: the request/reply-over-pub/sub pattern behind
``POST /api/robots/{robot_id}/cmd`` (plan-messaging.md Phase C). A fake stands in for
``aiomqtt.Client`` the same way ``test_gateway_streams.py``'s ``FakeBrowser`` stands in
for a WebSocket - no real broker involved."""

import asyncio
import json

import aiomqtt
import pytest
from conftest import load_module

mqtt_commands = load_module("gateway", "mqtt_commands")
CommandBus = mqtt_commands.CommandBus
CommandBusUnavailable = mqtt_commands.CommandBusUnavailable
CommandTimeout = mqtt_commands.CommandTimeout


class FakeMessage:
    def __init__(self, topic: str, payload: dict):
        self.topic = topic
        self.payload = json.dumps(payload).encode()


class FakeClient:
    """A fake aiomqtt.Client: records publishes, and lets a test push "incoming"
    messages in through ``inject()`` for the bus's background listener to consume."""

    instances: list["FakeClient"] = []

    def __init__(self, host, port, **kwargs):
        self.host, self.port = host, port
        self.published: list[tuple[str, str, int]] = []
        self.subscribed: list[str] = []
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._closed = False
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    async def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos))

    @property
    def messages(self):
        return self._iter_messages()

    async def _iter_messages(self):
        while True:
            item = await self._incoming.get()
            if item is _DISCONNECT:
                raise aiomqtt.MqttError("fake disconnect")
            yield item

    async def inject(self, message: FakeMessage):
        await self._incoming.put(message)

    async def disconnect(self):
        await self._incoming.put(_DISCONNECT)


_DISCONNECT = object()


@pytest.fixture(autouse=True)
def fake_aiomqtt(monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(mqtt_commands.aiomqtt, "Client", FakeClient)
    yield
    FakeClient.instances.clear()


async def _bus_connected() -> CommandBus:
    """Start a bus and wait until its fake client has connected and subscribed."""
    bus = CommandBus("localhost", 1883, reconnect_min=0.01, reconnect_max=0.02)
    bus.start()
    for _ in range(100):
        if bus.connected and FakeClient.instances and FakeClient.instances[-1].subscribed:
            break
        await asyncio.sleep(0.01)
    assert bus.connected, "bus never connected to the fake broker"
    return bus


def test_send_publishes_and_resolves_on_a_matching_ack():
    async def run():
        bus = await _bus_connected()
        client = FakeClient.instances[-1]
        try:
            send_task = asyncio.create_task(bus.send("robot-001", {"type": "estop"}, timeout=2))
            await asyncio.sleep(0.05)
            topic, payload, qos = client.published[0]
            command_id = json.loads(payload)["command_id"]
            assert topic == "neurafleet/robot-001/cmd" and qos == 1
            await client.inject(
                FakeMessage(
                    "neurafleet/robot-001/cmd/ack",
                    {
                        "command_id": command_id,
                        "robot_id": "robot-001",
                        "accepted": True,
                        "reason": None,
                        "timestamp": "t",
                    },
                )
            )
            ack = await send_task
            assert ack["accepted"] is True and ack["command_id"] == command_id
        finally:
            await bus.stop()

    asyncio.run(run())


def test_send_times_out_when_no_ack_arrives():
    async def run():
        bus = await _bus_connected()
        try:
            with pytest.raises(CommandTimeout):
                await bus.send("robot-999", {"type": "estop"}, timeout=0.1)
        finally:
            await bus.stop()

    asyncio.run(run())


def test_ack_for_a_different_command_id_does_not_resolve_an_unrelated_send():
    async def run():
        bus = await _bus_connected()
        client = FakeClient.instances[-1]
        try:
            send_task = asyncio.create_task(bus.send("robot-001", {"type": "estop"}, timeout=0.3))
            await asyncio.sleep(0.05)
            await client.inject(
                FakeMessage(
                    "neurafleet/robot-001/cmd/ack",
                    {"command_id": "not-the-right-one", "robot_id": "robot-001", "accepted": True},
                )
            )
            with pytest.raises(CommandTimeout):
                await send_task
        finally:
            await bus.stop()

    asyncio.run(run())


def test_send_raises_unavailable_before_any_connection():
    async def run():
        bus = CommandBus("localhost", 1883)  # never started
        with pytest.raises(CommandBusUnavailable):
            await bus.send("robot-001", {"type": "estop"})

    asyncio.run(run())


def test_bus_reconnects_after_a_dropped_connection():
    async def run():
        bus = await _bus_connected()
        first_client = FakeClient.instances[-1]
        try:
            await first_client.disconnect()
            for _ in range(200):
                if len(FakeClient.instances) > 1 and FakeClient.instances[-1].subscribed:
                    break
                await asyncio.sleep(0.01)
            assert len(FakeClient.instances) > 1, "bus never reconnected with a new client"
            assert bus.connected
        finally:
            await bus.stop()

    asyncio.run(run())


def test_malformed_ack_payload_is_ignored_not_raised():
    async def run():
        bus = await _bus_connected()
        client = FakeClient.instances[-1]
        try:
            bad = FakeMessage.__new__(FakeMessage)
            bad.topic = "neurafleet/robot-001/cmd/ack"
            bad.payload = b"{not json"
            await client.inject(bad)
            await asyncio.sleep(0.05)  # must not crash the listener task
            assert bus.connected
        finally:
            await bus.stop()

    asyncio.run(run())
