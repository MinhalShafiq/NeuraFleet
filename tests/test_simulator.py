"""FleetSimulator: reproducibility (plan 2.2) and stateful alerts (plan 0.5)."""

from conftest import load_module

sim_mod = load_module("telemetry-service", "robot_simulator")
FleetSimulator = sim_mod.FleetSimulator


def _strip_ts(rows):
    return [{k: v for k, v in r.items() if k != "timestamp"} for r in rows]


def _run(seed, ticks=200):
    sim = FleetSimulator(seed=seed)
    for _ in range(ticks):
        sim.update(0.1)
    return _strip_ts(sim.get_telemetry())


def test_same_seed_is_reproducible():
    assert _run(1) == _run(1)


def test_different_seed_differs():
    assert _run(1) != _run(2)


def _quiet_sim():
    """A sim whose robots are far apart and never inject random anomalies."""
    sim = FleetSimulator(seed=1)
    for i, r in enumerate(sim.robots.values()):
        r.x, r.y = 10.0 + 15 * i, 10.0
        r._anomaly_cooldown = 1e9
    return sim


def test_sustained_condition_raises_exactly_one_alert():
    sim = _quiet_sim()
    robot = next(iter(sim.robots.values()))
    for _ in range(300):  # 30 s of sim time
        robot.temperature = 80.0
        sim.update(0.1)
    temp = [a for a in sim.get_alerts() if a["type"] == "high_temperature"]
    assert len(temp) == 1
    assert temp[0]["count"] >= 290
    # Stable id across polls -> stable React keys
    assert sim.get_alerts()[0]["id"] == sim.get_alerts()[0]["id"]


def test_alert_clears_with_hysteresis_and_moves_to_history():
    sim = _quiet_sim()
    robot = next(iter(sim.robots.values()))

    def tick(temp):
        robot.temperature = temp
        sim.update(0.1)
        return [a for a in sim.get_alerts() if a["type"] == "high_temperature"]

    assert len(tick(80.0)) == 1
    assert len(tick(74.0)) == 1, "inside the hysteresis band the alert must not flap"
    assert len(tick(60.0)) == 0
    resolved = [a for a in sim.get_resolved_alerts() if a["type"] == "high_temperature"]
    assert len(resolved) == 1 and "resolved_at" in resolved[0]
    # Re-occurrence is a new event with a new id
    again = tick(80.0)
    assert len(again) == 1 and again[0]["id"] != resolved[0]["id"]


def test_alert_count_is_bounded_over_long_runs():
    sim = _quiet_sim()
    for robot in sim.robots.values():
        robot.temperature = 80.0
    for _ in range(100):
        for robot in sim.robots.values():
            robot.temperature = 80.0
        sim.update(0.1)
    assert len(sim.get_alerts()) <= len(sim.robots) * 5
