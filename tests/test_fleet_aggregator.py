"""FleetAggregator: filling robot state from MQTT messages instead of local physics
(plan-messaging.md Phase A/B) without losing any of FleetSimulator's alert/metrics
behaviour, plus the new per-robot connectivity tracking."""

import time

import pytest
from conftest import load_module

sim_mod = load_module("telemetry-service", "robot_simulator")
FleetAggregator = sim_mod.FleetAggregator

ROBOT = "robot-001"


def _telemetry(**overrides) -> dict:
    """A valid telemetry payload (the exact shape robot_agent publishes)."""
    base = {
        "robot_id": ROBOT,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "position": {"x": 12.0, "y": 34.0, "z": 0.0},
        "velocity": {"vx": 1.0, "vy": 0.0, "vz": 0.0},
        "battery": 80.0,
        "status": "active",
        "cpu_usage": 40.0,
        "memory_usage": 45.0,
        "temperature": 50.0,
        "sensors": {
            "imu": {"ax": 0.1, "ay": 0.0, "az": -9.81, "gx": 0.0, "gy": 0.0, "gz": 0.0},
            "gps": {"lat": 37.0, "lon": -122.0, "alt": 10.0},
        },
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- ingest fills state


def test_ingest_telemetry_updates_the_matching_robot():
    agg = FleetAggregator()
    agg.ingest_telemetry(ROBOT, _telemetry(battery=55.5, position={"x": 7.0, "y": 8.0, "z": 0.0}))
    robot = agg.get_robot(ROBOT)
    assert robot["battery"] == 55.5
    assert robot["position"] == {"x": 7.0, "y": 8.0, "z": 0.0}


def test_ingest_telemetry_for_unknown_robot_id_is_ignored_not_raised():
    agg = FleetAggregator()
    agg.ingest_telemetry("robot-999", _telemetry(robot_id="robot-999"))  # must not raise
    assert agg.get_robot("robot-999") is None


def test_ingest_telemetry_with_missing_fields_raises_keyerror_for_the_caller_to_catch():
    """The MQTT bridge is responsible for catching this (a malformed message shouldn't
    take down the aggregator); the aggregator itself fails loudly rather than silently
    accepting half a robot's state."""
    agg = FleetAggregator()
    with pytest.raises(KeyError):
        agg.ingest_telemetry(ROBOT, {"robot_id": ROBOT})


# --------------------------------------------------------------- connectivity


def test_fresh_aggregator_is_online_before_any_message_arrives():
    """Optimistic at startup: nothing has gone wrong yet, so health should not be
    "degraded" the instant the process starts, before agents have had time to connect."""
    agg = FleetAggregator()
    assert agg.connectivity(ROBOT) == "online"
    assert agg.connectivity_counts()["online"] == len(agg.robots)


def test_ingest_marks_the_robot_online():
    agg = FleetAggregator()
    agg.mark_status(ROBOT, online=False)
    assert agg.connectivity(ROBOT) == "offline"
    agg.ingest_telemetry(ROBOT, _telemetry())
    assert agg.connectivity(ROBOT) == "online"


def test_status_message_marks_offline_immediately():
    agg = FleetAggregator()
    agg.ingest_telemetry(ROBOT, _telemetry())
    assert agg.connectivity(ROBOT) == "online"
    agg.mark_status(ROBOT, online=False)
    assert agg.connectivity(ROBOT) == "offline"


def test_check_staleness_ages_a_silent_robot_to_stale_then_offline():
    agg = FleetAggregator()
    agg.ingest_telemetry(ROBOT, _telemetry())
    conn = agg._conn[ROBOT]

    conn.last_seen = time.monotonic() - (sim_mod.STALE_AFTER_SECONDS + 0.5)
    agg.check_staleness()
    assert agg.connectivity(ROBOT) == "stale"

    conn.last_seen = time.monotonic() - (sim_mod.OFFLINE_AFTER_SECONDS + 0.5)
    agg.check_staleness()
    assert agg.connectivity(ROBOT) == "offline"


def test_explicit_offline_is_not_overridden_by_the_staleness_timer():
    """An LWT/status "offline" message is authoritative - it must not flip back to
    "online"/"stale" just because check_staleness() runs again soon after."""
    agg = FleetAggregator()
    agg.mark_status(ROBOT, online=False)
    agg.check_staleness()
    assert agg.connectivity(ROBOT) == "offline"


def test_connectivity_counts_sums_to_fleet_size():
    agg = FleetAggregator()
    agg.mark_status(ROBOT, online=False)
    counts = agg.connectivity_counts()
    assert sum(counts.values()) == len(agg.robots)
    assert counts["offline"] == 1
    assert counts["online"] == len(agg.robots) - 1


def test_unknown_robot_in_mark_status_is_ignored():
    agg = FleetAggregator()
    agg.mark_status("robot-999", online=False)  # must not raise


# --------------------------------------------------------------- query shape: additive


def test_connectivity_field_is_present_on_every_query_method():
    agg = FleetAggregator()
    agg.ingest_telemetry(ROBOT, _telemetry())
    assert agg.get_robot(ROBOT)["connectivity"] == "online"
    assert all("connectivity" in r for r in agg.get_all_robots())
    assert all("connectivity" in r for r in agg.get_telemetry())


# --------------------------------------------------------------- alerts still work


def test_alerts_still_raise_from_ingested_telemetry():
    """The existing stateful-alert engine (FleetSimulator._detect_anomalies/_sync_alerts)
    must keep working when fed from MQTT instead of a local tick."""
    agg = FleetAggregator()
    for i, r in enumerate(agg.robots.values()):
        r.x, r.y = 10.0 + 15 * i, 10.0  # keep everyone far apart; no collision-proximity noise

    agg.ingest_telemetry(ROBOT, _telemetry(temperature=90.0))
    alerts = [a for a in agg.get_alerts() if a["type"] == "high_temperature"]
    assert len(alerts) == 1 and alerts[0]["robot_id"] == ROBOT
    assert alerts[0]["severity"] == "critical"


def test_alerts_clear_once_ingested_telemetry_recovers():
    agg = FleetAggregator()
    agg.ingest_telemetry(ROBOT, _telemetry(temperature=90.0))
    assert any(a["type"] == "high_temperature" for a in agg.get_alerts())
    agg.ingest_telemetry(ROBOT, _telemetry(temperature=40.0))
    assert not any(a["type"] == "high_temperature" for a in agg.get_alerts())
    assert any(a["type"] == "high_temperature" for a in agg.get_resolved_alerts())


# --------------------------------------------------------------- update() still works


def test_update_still_works_for_a_bare_or_aggregator_instance():
    """FleetAggregator IS a FleetSimulator; nothing about the MQTT-filling behaviour
    should break the inherited local-tick path (tests, or a non-MQTT dev mode)."""
    agg = FleetAggregator(seed=1)
    for _ in range(50):
        agg.update(0.1)
    assert len(agg.get_all_robots()) == 6
    # update() never touches connectivity - only ingest_telemetry/mark_status/
    # check_staleness do - so robots stay "online" (the optimistic startup default).
    assert agg.connectivity_counts()["online"] == 6


def test_ingest_telemetry_carries_estopped_through():
    agg = FleetAggregator()
    agg.ingest_telemetry(ROBOT, _telemetry(estopped=True))
    assert agg.get_robot(ROBOT)["estopped"] is True


def test_ingest_telemetry_without_estopped_key_defaults_false():
    """Forward/backward compatibility: an older agent payload without "estopped" must
    not raise a KeyError."""
    agg = FleetAggregator()
    payload = _telemetry()
    payload.pop("estopped", None)
    agg.ingest_telemetry(ROBOT, payload)
    assert agg.get_robot(ROBOT)["estopped"] is False
