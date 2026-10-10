"""
CommandBus: the gateway's cloud -> robot write path (plan-messaging.md Phase C).

``POST /api/robots/{robot_id}/cmd`` publishes to ``neurafleet/{robot_id}/cmd`` and waits
for the matching ``neurafleet/{robot_id}/cmd/ack`` - a request/reply pattern built on top
of plain pub/sub, with no message broker feature beyond topics and QoS 1 needed for it.

One long-lived connection subscribes to every robot's ack topic and resolves whichever
pending ``asyncio.Future`` matches the incoming ``command_id``; ``send()`` publishes the
command and awaits its future with a timeout. A command to a robot that never answers
(dead agent, no such robot's agent connected) times out rather than hanging forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import uuid
from typing import Any

import aiomqtt

logger = logging.getLogger("gateway.mqtt_commands")

# See telemetry_service/mqtt_bridge.py: paho's own internal logger, quieted because we
# already log reconnects ourselves at the right level.
logging.getLogger("mqtt").setLevel(logging.CRITICAL)


class CommandTimeout(Exception):
    """No ack arrived within the deadline (the robot's agent is down or unreachable)."""


class CommandBusUnavailable(Exception):
    """The gateway itself has no MQTT connection right now."""


class CommandBus:
    def __init__(
        self, host: str, port: int, *, reconnect_min: float = 1.0, reconnect_max: float = 15.0
    ):
        self._host = host
        self._port = port
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._client: aiomqtt.Client | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        self.connected = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()

    async def _run(self) -> None:
        delay = self._reconnect_min
        while True:
            try:
                async with aiomqtt.Client(
                    self._host, self._port, identifier="gateway-commands", keepalive=10
                ) as client:
                    self._client = client
                    self.connected = True
                    delay = self._reconnect_min
                    logger.info("Connected to MQTT broker %s:%s", self._host, self._port)
                    await client.subscribe("neurafleet/+/cmd/ack", qos=1)
                    async for message in client.messages:
                        self._on_ack(message)
            except asyncio.CancelledError:
                raise
            # See telemetry_service/mqtt_bridge.py: a severed connection can surface as
            # a bare OSError as well as aiomqtt.MqttError - both are "reconnect", not a bug.
            except (aiomqtt.MqttError, OSError) as exc:
                logger.warning(
                    "MQTT command bus connection lost (%s); retrying in %.1fs", exc, delay
                )
            except Exception:
                logger.exception("Unexpected error in the command bus")
            finally:
                self.connected = False
                self._client = None
            await asyncio.sleep(delay + random.uniform(0, delay * 0.25))
            delay = min(delay * 2, self._reconnect_max)

    def _on_ack(self, message: aiomqtt.Message) -> None:
        try:
            payload = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return
        command_id = payload.get("command_id")
        fut = self._pending.get(command_id)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    async def send(self, robot_id: str, command: dict[str, Any], timeout: float = 2.0) -> dict:
        """Publish *command* to *robot_id* and wait for its ack.

        Raises ``CommandBusUnavailable`` if the gateway has no broker connection at all
        (a true infrastructure failure, distinct from "the robot didn't answer"), and
        ``CommandTimeout`` if the command was sent but nothing replied in time - which
        is the normal, expected outcome for a robot that is offline.
        """
        client = self._client
        if client is None or not self.connected:
            raise CommandBusUnavailable("no MQTT connection")

        command_id = uuid.uuid4().hex
        command = {**command, "command_id": command_id}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[command_id] = fut
        try:
            await client.publish(f"neurafleet/{robot_id}/cmd", json.dumps(command), qos=1)
            try:
                return await asyncio.wait_for(fut, timeout=timeout)
            except TimeoutError as exc:
                raise CommandTimeout(
                    f"no ack from {robot_id} within {timeout}s (its agent may be offline)"
                ) from exc
        finally:
            self._pending.pop(command_id, None)
