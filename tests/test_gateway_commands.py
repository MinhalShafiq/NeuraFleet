"""Gateway's POST /api/robots/{robot_id}/cmd (plan-messaging.md Phase C): the three
outcomes - unknown robot (404), no broker at all (503), and a known robot that simply
didn't answer (200, accepted=False) - are deliberately different HTTP statuses."""

import asyncio

import httpx
from conftest import load_module

gw = load_module("gateway", "main")
# gw.py did `from mqtt_commands import CommandBusUnavailable, CommandTimeout`, so these
# are the exact classes its `except` clauses match against. A second, separate
# load_module("gateway", "mqtt_commands") would create a DIFFERENT module object (and
# therefore different, non-matching exception classes) - see conftest.load_module.
CommandBusUnavailable = gw.CommandBusUnavailable
CommandTimeout = gw.CommandTimeout


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


def _post(body, robot_id="robot-001"):
    async def run():
        async with _client() as c:
            return await c.post(f"/api/robots/{robot_id}/cmd", json=body)

    return asyncio.run(run())


class FakeBus:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    async def send(self, robot_id, command, timeout):
        self.calls.append((robot_id, command, timeout))
        if self._exc:
            raise self._exc
        return self._result


def test_unknown_robot_is_404_even_with_no_bus_configured(monkeypatch):
    monkeypatch.setattr(gw, "command_bus", None)
    r = _post({"type": "estop"}, robot_id="robot-does-not-exist")
    assert r.status_code == 404


def test_no_command_bus_is_503(monkeypatch):
    monkeypatch.setattr(gw, "command_bus", None)
    r = _post({"type": "estop"})
    assert r.status_code == 503


def test_accepted_command_returns_the_ack(monkeypatch):
    ack = {
        "command_id": "abc",
        "robot_id": "robot-001",
        "accepted": True,
        "reason": None,
        "timestamp": "t",
    }
    bus = FakeBus(result=ack)
    monkeypatch.setattr(gw, "command_bus", bus)
    r = _post({"type": "estop"})
    assert r.status_code == 200
    assert r.json() == ack
    assert bus.calls[0][0] == "robot-001"
    assert bus.calls[0][1]["type"] == "estop"
    # None-valued optional fields (x/y/speed_limit) are not sent as a command payload
    assert "x" not in bus.calls[0][1]


def test_bus_unavailable_is_503(monkeypatch):
    bus = FakeBus(exc=CommandBusUnavailable("no connection"))
    monkeypatch.setattr(gw, "command_bus", bus)
    r = _post({"type": "estop"})
    assert r.status_code == 503


def test_timeout_is_200_with_accepted_false_not_an_error(monkeypatch):
    """A robot that doesn't answer is the normal shape of a distributed system, not a
    gateway failure - the caller gets a complete, honest answer, not a 5xx."""
    bus = FakeBus(exc=CommandTimeout("no ack"))
    monkeypatch.setattr(gw, "command_bus", bus)
    r = _post({"type": "estop"})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is False and "offline" in body["reason"]


def test_set_goal_passes_through_coordinates(monkeypatch):
    bus = FakeBus(
        result={
            "command_id": "x",
            "robot_id": "robot-002",
            "accepted": True,
            "reason": None,
            "timestamp": "t",
        }
    )
    monkeypatch.setattr(gw, "command_bus", bus)
    r = _post({"type": "set_goal", "x": 5.0, "y": -3.0}, robot_id="robot-002")
    assert r.status_code == 200
    assert bus.calls[0][1] == {"type": "set_goal", "x": 5.0, "y": -3.0}


def test_invalid_command_type_is_422():
    r = _post({"type": "fly"})
    assert r.status_code == 422


def test_negative_speed_limit_is_422():
    r = _post({"type": "set_speed_limit", "speed_limit": -1})
    assert r.status_code == 422
