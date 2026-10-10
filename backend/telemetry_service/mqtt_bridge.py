"""
MQTT bridge for telemetry-service (plan-messaging.md Phase A).

Subscribes to every robot agent's telemetry and status topics and feeds them into a
``FleetAggregator``. This is the only place telemetry-service talks MQTT; everything
else (alerts, metrics history, the REST/WebSocket API) is unchanged by where the
numbers came from - see ``robot_simulator.FleetAggregator``.

Topics (see plan-messaging.md section 4.1):
  neurafleet/{robot_id}/telemetry   QoS 0, 10 Hz    - a dropped sample is replaced in 100ms
  neurafleet/{robot_id}/status      QoS 1, retained - "online"/"offline"; also the LWT target
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import aiomqtt
from robot_simulator import FleetAggregator

logger = logging.getLogger("telemetry_service.mqtt")

# aiomqtt/paho log routine disconnects (a broken socket when the broker container
# stops) through their own "mqtt" logger at ERROR level, by paho's own classification -
# independent of the exception handling below, and duplicating what MqttBridge.run()
# already logs at WARNING once it catches the resulting error. Quieted so an expected
# reconnect doesn't read as an unhandled error in the service's logs.
logging.getLogger("mqtt").setLevel(logging.CRITICAL)


class MqttBridge:
    """Owns one long-lived MQTT connection; reconnects with jittered exponential
    backoff when it drops, same shape as the gateway's WebSocket relay fallback."""

    def __init__(
        self,
        aggregator: FleetAggregator,
        host: str,
        port: int,
        *,
        reconnect_min: float = 1.0,
        reconnect_max: float = 15.0,
    ):
        self._agg = aggregator
        self._host = host
        self._port = port
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self.connected = False

    async def run(self) -> None:
        """Connect, subscribe, consume messages forever. Cancel the task to stop."""
        delay = self._reconnect_min
        while True:
            try:
                async with aiomqtt.Client(
                    self._host,
                    self._port,
                    identifier="telemetry-aggregator",
                    keepalive=10,
                ) as client:
                    self.connected = True
                    delay = self._reconnect_min
                    logger.info("Connected to MQTT broker %s:%s", self._host, self._port)
                    await client.subscribe("neurafleet/+/telemetry", qos=0)
                    await client.subscribe("neurafleet/+/status", qos=1)
                    async for message in client.messages:
                        self._handle(message)
            except asyncio.CancelledError:
                raise
            # aiomqtt wraps most broker-level failures in MqttError, but a severed TCP
            # connection (the broker container stopping mid-read) can also surface as a
            # bare OSError (BrokenPipeError, ConnectionResetError, ...) - both are the
            # same "the connection is gone, reconnect" condition, so both log at warning,
            # not error. Anything else is a real bug and gets the full traceback.
            except (aiomqtt.MqttError, OSError) as exc:
                logger.warning("MQTT connection lost (%s); retrying in %.1fs", exc, delay)
            except Exception:
                logger.exception("Unexpected error in the MQTT bridge")
            finally:
                self.connected = False
            await asyncio.sleep(delay + random.uniform(0, delay * 0.25))
            delay = min(delay * 2, self._reconnect_max)

    def _handle(self, message: aiomqtt.Message) -> None:
        parts = str(message.topic).split("/")
        if len(parts) != 3 or parts[0] != "neurafleet":
            return
        _, robot_id, kind = parts
        try:
            payload = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            logger.warning("Dropping malformed MQTT payload on %s", message.topic)
            return

        if kind == "telemetry":
            try:
                self._agg.ingest_telemetry(robot_id, payload)
            except (KeyError, TypeError) as exc:
                logger.warning("Dropping malformed telemetry for %s: %s", robot_id, exc)
        elif kind == "status":
            self._agg.mark_status(robot_id, online=payload.get("state") == "online")
