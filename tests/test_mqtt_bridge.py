"""telemetry-service's MqttBridge: topic parsing and dispatch to the aggregator
(plan-messaging.md Phase A). No real broker involved - ``_handle`` takes anything with a
``.topic``/``.payload`` pair, so a tiny stand-in is enough."""

import json
from dataclasses import dataclass

from conftest import load_module

bridge_mod = load_module("telemetry-service", "mqtt_bridge")
sim_mod = load_module("telemetry-service", "robot_simulator")

MqttBridge = bridge_mod.MqttBridge
FleetAggregator = sim_mod.FleetAggregator


@dataclass
class FakeMessage:
    topic: str
    payload: bytes


def _telemetry_payload(**overrides) -> bytes:
    base = {
        "robot_id": "robot-001",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "position": {"x": 1.0, "y": 2.0, "z": 0.0},
        "velocity": {"vx": 0.0, "vy": 0.0, "vz": 0.0},
        "battery": 70.0,
        "status": "active",
        "cpu_usage": 30.0,
        "memory_usage": 40.0,
        "temperature": 45.0,
        "sensors": {
            "imu": {"ax": 0, "ay": 0, "az": -9.81, "gx": 0, "gy": 0, "gz": 0},
            "gps": {"lat": 37.0, "lon": -122.0, "alt": 10.0},
        },
    }
    base.update(overrides)
    return json.dumps(base).encode()


def test_telemetry_message_is_routed_to_ingest_telemetry():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(FakeMessage("neurafleet/robot-001/telemetry", _telemetry_payload(battery=33.0)))
    assert agg.get_robot("robot-001")["battery"] == 33.0


def test_status_online_message_is_routed_to_mark_status():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(
        FakeMessage("neurafleet/robot-001/status", json.dumps({"state": "online"}).encode())
    )
    assert agg.connectivity("robot-001") == "online"


def test_status_offline_message_is_routed_to_mark_status():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(
        FakeMessage("neurafleet/robot-001/status", json.dumps({"state": "offline"}).encode())
    )
    assert agg.connectivity("robot-001") == "offline"


def test_malformed_json_payload_is_dropped_not_raised():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(FakeMessage("neurafleet/robot-001/telemetry", b"{not json"))  # must not raise


def test_telemetry_missing_required_fields_is_dropped_not_raised():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(
        FakeMessage(
            "neurafleet/robot-001/telemetry", json.dumps({"robot_id": "robot-001"}).encode()
        )
    )


def test_unexpected_topic_shape_is_ignored():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(FakeMessage("some/other/topic/shape", b"{}"))  # 4 parts, not 3 - ignored
    bridge._handle(FakeMessage("other/robot-001/telemetry", _telemetry_payload()))  # wrong prefix
    assert agg.get_robot("robot-001")["battery"] != 0  # unchanged from its init default-ish state


def test_unknown_robot_id_in_telemetry_topic_is_ignored_not_raised():
    agg = FleetAggregator()
    bridge = MqttBridge(agg, "localhost", 1883)
    bridge._handle(
        FakeMessage("neurafleet/robot-999/telemetry", _telemetry_payload(robot_id="robot-999"))
    )
    assert agg.get_robot("robot-999") is None
