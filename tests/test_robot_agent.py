"""robot_agent: command handling (plan-messaging.md Phase C) and the per-agent RNG/seed
derivation (plan-messaging.md Phase A) - everything that doesn't need a real broker."""

import math
import os

import pytest
from conftest import load_robot_agent_module

# robot_agent/main.py reads ROBOT_ID at import time (it fails fast with no default - a
# container with no identity is a bug, not something to paper over) - set it before the
# first load_robot_agent_module() call imports the real module body.
os.environ.setdefault("ROBOT_ID", "robot-001")

from shared.robot_physics import RobotSim  # noqa: E402  (sys.path set up by conftest)

agent_main = load_robot_agent_module("main")
commands = load_robot_agent_module("commands")

apply_command = commands.apply_command
CommandDeduplicator = commands.CommandDeduplicator


def _sim(**overrides) -> RobotSim:
    base = dict(robot_id="robot-001", name="Atlas-1", robot_type="explorer")
    base.update(overrides)
    return RobotSim(**base)


# --------------------------------------------------------------- apply_command


def test_estop_sets_the_flag():
    sim = _sim()
    ack = apply_command(sim, {"command_id": "c1", "type": "estop"}, "t")
    assert sim.estopped is True
    assert ack == {
        "command_id": "c1",
        "robot_id": "robot-001",
        "accepted": True,
        "reason": None,
        "timestamp": "t",
    }


def test_resume_clears_the_flag():
    sim = _sim(estopped=True)
    ack = apply_command(sim, {"command_id": "c2", "type": "resume"}, "t")
    assert sim.estopped is False
    assert ack["accepted"] is True


def test_set_goal_with_valid_coordinates():
    sim = _sim()
    ack = apply_command(sim, {"command_id": "c3", "type": "set_goal", "x": 5.0, "y": -2.5}, "t")
    assert (sim.goal_x, sim.goal_y) == (5.0, -2.5)
    assert ack["accepted"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"type": "set_goal"},
        {"type": "set_goal", "x": 1.0},
        {"type": "set_goal", "x": "nope", "y": 1},
    ],
)
def test_set_goal_without_numeric_x_and_y_is_rejected(body):
    sim = _sim()
    ack = apply_command(sim, {"command_id": "c4", **body}, "t")
    assert ack["accepted"] is False and ack["reason"]
    assert sim.goal_x is None and sim.goal_y is None


def test_set_speed_limit_with_a_valid_value():
    sim = _sim()
    ack = apply_command(
        sim, {"command_id": "c5", "type": "set_speed_limit", "speed_limit": 0.5}, "t"
    )
    assert sim.speed_limit == 0.5
    assert ack["accepted"] is True


@pytest.mark.parametrize("limit", [-1, "fast", None])
def test_set_speed_limit_rejects_negative_or_non_numeric(limit):
    sim = _sim()
    ack = apply_command(
        sim, {"command_id": "c6", "type": "set_speed_limit", "speed_limit": limit}, "t"
    )
    assert ack["accepted"] is False
    assert sim.speed_limit is None


def test_unknown_command_type_is_rejected_not_raised():
    sim = _sim()
    ack = apply_command(sim, {"command_id": "c7", "type": "do_a_backflip"}, "t")
    assert ack["accepted"] is False and "unknown" in ack["reason"]


def test_missing_type_is_rejected_not_raised():
    sim = _sim()
    ack = apply_command(sim, {"command_id": "c8"}, "t")
    assert ack["accepted"] is False


# --------------------------------------------------------------- CommandDeduplicator


def test_duplicate_command_id_is_applied_only_once():
    sim = _sim()
    dedup = CommandDeduplicator()
    dedup.handle(sim, {"command_id": "same", "type": "set_goal", "x": 1.0, "y": 1.0}, "t1")
    # A redelivery with different (wrong) params must NOT be re-applied - the cached ack
    # for "same" is replayed instead, and the robot's state is untouched by the second call.
    second = dedup.handle(
        sim, {"command_id": "same", "type": "set_goal", "x": 99.0, "y": 99.0}, "t2"
    )
    assert (sim.goal_x, sim.goal_y) == (1.0, 1.0)
    assert second["timestamp"] == "t1"  # the FIRST ack, replayed


def test_different_command_ids_both_apply():
    sim = _sim()
    dedup = CommandDeduplicator()
    dedup.handle(sim, {"command_id": "a", "type": "estop"}, "t1")
    dedup.handle(sim, {"command_id": "b", "type": "resume"}, "t2")
    assert sim.estopped is False


def test_deduplicator_is_bounded():
    dedup = CommandDeduplicator(capacity=5)
    sim = _sim()
    for i in range(20):
        dedup.handle(sim, {"command_id": f"id-{i}", "type": "estop"}, "t")
    assert len(dedup._seen) == 5
    assert "id-19" in dedup._seen and "id-0" not in dedup._seen


def test_command_without_an_id_is_never_cached():
    dedup = CommandDeduplicator()
    sim = _sim()
    dedup.handle(sim, {"type": "estop"}, "t")
    assert len(dedup._seen) == 0


# --------------------------------------------------------------- per-agent seeding


def test_init_robot_is_deterministic_for_the_same_id_and_seed():
    r1, _ = agent_main.init_robot("robot-002", 12345, 100.0)
    r2, _ = agent_main.init_robot("robot-002", 12345, 100.0)
    assert (r1.x, r1.y, r1.battery, r1.heading) == (r2.x, r2.y, r2.battery, r2.heading)


def test_init_robot_differs_across_robot_ids_sharing_one_seed():
    """Six agents all read the same FLEET_SEED; without mixing the robot id in, they
    would all draw the identical starting position."""
    r1, _ = agent_main.init_robot("robot-001", 12345, 100.0)
    r2, _ = agent_main.init_robot("robot-002", 12345, 100.0)
    assert (r1.x, r1.y) != (r2.x, r2.y)


def test_init_robot_uses_the_known_fleet_config():
    r, _ = agent_main.init_robot("robot-003", 12345, 100.0)
    assert r.name == "Hauler-3" and r.robot_type == "hauler"


def test_init_robot_rejects_an_unknown_id():
    with pytest.raises(ValueError):
        agent_main.init_robot("robot-does-not-exist", 12345, 100.0)


def test_init_robot_stays_within_the_configured_area():
    r, _ = agent_main.init_robot("robot-004", 999, 100.0)
    assert 10 <= r.x <= 90 and 10 <= r.y <= 90
    assert 0 <= r.heading <= 2 * math.pi
