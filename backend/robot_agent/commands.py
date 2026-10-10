"""
Command handling for one robot agent (plan-messaging.md Phase C).

Pure and MQTT-free on purpose: ``apply_command`` and ``CommandDeduplicator`` are unit
tested directly, with no broker involved. ``main.py`` is the only place that touches
MQTT, and it just calls through to this module.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from shared.robot_physics import RobotSim

VALID_TYPES = {"set_goal", "estop", "resume", "set_speed_limit"}


def apply_command(sim: RobotSim, command: dict[str, Any], timestamp: str) -> dict[str, Any]:
    """Apply one command to *sim* in place and return the ack to publish.

    Unknown or malformed commands are rejected (``accepted: False`` with a reason),
    never raised - a bad command from a buggy caller must not crash the agent.
    """
    command_id = str(command.get("command_id", ""))
    ctype = command.get("type")
    accepted, reason = True, None

    if ctype == "estop":
        sim.estopped = True
    elif ctype == "resume":
        sim.estopped = False
    elif ctype == "set_goal":
        x, y = command.get("x"), command.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            accepted, reason = False, "set_goal requires numeric x and y"
        else:
            sim.goal_x, sim.goal_y = float(x), float(y)
    elif ctype == "set_speed_limit":
        limit = command.get("speed_limit")
        if not isinstance(limit, (int, float)) or limit < 0:
            accepted, reason = False, "speed_limit must be a number >= 0"
        else:
            sim.speed_limit = float(limit)
    elif ctype in VALID_TYPES:
        accepted, reason = False, f"{ctype} is recognised but not implemented"
    else:
        accepted, reason = False, f"unknown command type {ctype!r}"

    return {
        "command_id": command_id,
        "robot_id": sim.robot_id,
        "accepted": accepted,
        "reason": reason,
        "timestamp": timestamp,
    }


class CommandDeduplicator:
    """Remembers the last *capacity* command ids and their acks.

    QoS 1 only promises "at least once": the same command can arrive twice (a
    redelivery after a slow ack, a gateway retry). Re-applying ``estop`` twice is
    harmless, but ``set_goal`` applied twice from a replayed *old* message could
    overwrite a newer command that arrived in between. Deduplicating by id - replay
    the cached ack, don't re-apply - makes QoS 1 safe without needing QoS 2.
    """

    def __init__(self, capacity: int = 200):
        self._capacity = capacity
        self._seen: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def handle(self, sim: RobotSim, command: dict[str, Any], timestamp: str) -> dict[str, Any]:
        command_id = command.get("command_id")
        if command_id and command_id in self._seen:
            return self._seen[command_id]

        ack = apply_command(sim, command, timestamp)

        if command_id:
            self._seen[command_id] = ack
            if len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
        return ack
