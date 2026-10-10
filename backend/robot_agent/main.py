"""
NeuraFleet Robot Agent
======================

Simulates ONE robot and publishes it over MQTT (plan-messaging.md Phase A). Six of
these (one per ``ROBOT_ID``) replace the single in-process ``FleetSimulator`` loop that
telemetry-service used to run; telemetry-service now just aggregates what arrives here.

Topics published:
  neurafleet/{robot_id}/telemetry     QoS 0, 10 Hz
  neurafleet/{robot_id}/status        QoS 1, retained ("online" on connect, the broker
                                       publishes "offline" on our behalf via the Last
                                       Will if this process disappears uncleanly)
Topics subscribed:
  neurafleet/{robot_id}/cmd           QoS 1  ->  neurafleet/{robot_id}/cmd/ack
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiomqtt
from commands import CommandDeduplicator
from dotenv import load_dotenv
from fastapi import FastAPI

from shared.observability import install_observability
from shared.robot_physics import FLEET_CONFIG, RobotConfig, RobotSim, advance_robot, robot_telemetry

load_dotenv()
logger = logging.getLogger("robot_agent")

# See telemetry_service/mqtt_bridge.py: paho's own internal logger, quieted because we
# already log reconnects ourselves at the right level.
logging.getLogger("mqtt").setLevel(logging.CRITICAL)

ROBOT_ID = os.environ["ROBOT_ID"]  # fail fast: a container with no identity is a bug, not a default
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
AREA_SIZE = float(os.getenv("AREA_SIZE", "100.0"))
FLEET_SEED = int(os.getenv("FLEET_SEED", "12345"))
TICK_HZ = 10.0
RECONNECT_MIN, RECONNECT_MAX = 1.0, 15.0


def _config_for(robot_id: str) -> RobotConfig:
    for cfg in FLEET_CONFIG:
        if cfg.robot_id == robot_id:
            return cfg
    raise ValueError(
        f"ROBOT_ID={robot_id!r} is not in shared.robot_physics.FLEET_CONFIG "
        f"(known ids: {[c.robot_id for c in FLEET_CONFIG]})"
    )


def init_robot(robot_id: str, seed: int, area_size: float) -> tuple[RobotSim, random.Random]:
    """Build this robot's initial state and its own RNG stream.

    Each agent is its own process, so (unlike the old one-process FleetSimulator,
    where all six robots drew from one shared ``random.Random``) every robot needs an
    independent, reproducible seed. ``zlib.crc32`` is the same process-independent
    substitute for ``hash(str)`` that lidar_service already uses for this.
    """
    cfg = _config_for(robot_id)
    rng = random.Random(zlib.crc32(robot_id.encode()) ^ seed)
    r = RobotSim(
        robot_id=cfg.robot_id,
        name=cfg.name,
        robot_type=cfg.robot_type,
        x=rng.uniform(10, area_size - 10),
        y=rng.uniform(10, area_size - 10),
        z=0.0,
        vx=rng.uniform(-0.5, 0.5),
        vy=rng.uniform(-0.5, 0.5),
        battery=rng.uniform(70, 100),
        heading=rng.uniform(0, 2 * 3.141592653589793),
        cpu_usage=rng.uniform(20, 50),
        memory_usage=rng.uniform(30, 60),
        temperature=rng.uniform(40, 60),
        _charge_x=rng.uniform(20, 80),
        _charge_y=rng.uniform(20, 80),
    )
    r.gps_lat = 37.7749 + (r.x - 50) * 0.00001
    r.gps_lon = -122.4194 + (r.y - 50) * 0.00001
    return r, rng


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

sim, rng = init_robot(ROBOT_ID, FLEET_SEED, AREA_SIZE)
dedup = CommandDeduplicator()
mqtt_connected = False
_sim_time = 0.0
_tasks: list[asyncio.Task] = []


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _status_payload(online: bool) -> str:
    return json.dumps(
        {"robot_id": ROBOT_ID, "state": "online" if online else "offline", "timestamp": _now()}
    )


async def _publish_loop(client: aiomqtt.Client) -> None:
    """Tick the physics at 10 Hz and publish telemetry. QoS 0: a dropped sample is
    replaced 100ms later, so paying for an ack on every message buys nothing."""
    global _sim_time
    dt = 1.0 / TICK_HZ
    topic = f"neurafleet/{ROBOT_ID}/telemetry"
    while True:
        _sim_time += dt
        advance_robot(sim, dt, rng, AREA_SIZE, _sim_time)
        await client.publish(topic, json.dumps(robot_telemetry(sim, _now())), qos=0)
        await asyncio.sleep(dt)


async def _command_loop(client: aiomqtt.Client) -> None:
    """Apply incoming commands and ack them. QoS 1: a lost e-stop is unacceptable, a
    redelivered one is fine - ``CommandDeduplicator`` makes redelivery safe."""
    ack_topic = f"neurafleet/{ROBOT_ID}/cmd/ack"
    async for message in client.messages:
        try:
            command = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            logger.warning("Dropping malformed command payload: %r", message.payload)
            continue
        ack = dedup.handle(sim, command, _now())
        logger.info(
            "Command %s: accepted=%s reason=%s", ack["command_id"], ack["accepted"], ack["reason"]
        )
        await client.publish(ack_topic, json.dumps(ack), qos=1)


async def _mqtt_loop() -> None:
    global mqtt_connected
    delay = RECONNECT_MIN
    will = aiomqtt.Will(
        topic=f"neurafleet/{ROBOT_ID}/status", payload=_status_payload(False), qos=1, retain=True
    )
    while True:
        try:
            async with aiomqtt.Client(
                MQTT_HOST, MQTT_PORT, identifier=f"agent-{ROBOT_ID}", will=will, keepalive=2
            ) as client:
                mqtt_connected = True
                delay = RECONNECT_MIN
                logger.info("%s connected to MQTT broker %s:%s", ROBOT_ID, MQTT_HOST, MQTT_PORT)
                await client.publish(
                    f"neurafleet/{ROBOT_ID}/status", _status_payload(True), qos=1, retain=True
                )
                await client.subscribe(f"neurafleet/{ROBOT_ID}/cmd", qos=1)
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(_publish_loop(client))
                    tg.create_task(_command_loop(client))
        # A TaskGroup wraps its children's exceptions in an ExceptionGroup, so this needs
        # except* rather than except; a plain asyncio.CancelledError (e.g. from the
        # lifespan cancelling this task on shutdown) still propagates through it as a
        # BaseException, which neither clause below matches - exactly as a plain `except
        # Exception` would never catch it either. A severed connection can surface as a
        # bare OSError as well as aiomqtt.MqttError (see telemetry_service/mqtt_bridge.py).
        except* (aiomqtt.MqttError, OSError) as excgroup:
            logger.warning(
                "%s: MQTT connection lost (%s); retrying in %.1fs",
                ROBOT_ID,
                excgroup.exceptions[0],
                delay,
            )
        except* Exception:
            logger.exception("%s: unexpected error in the MQTT loop", ROBOT_ID)
        finally:
            mqtt_connected = False
        await asyncio.sleep(delay + random.uniform(0, delay * 0.25))
        delay = min(delay * 2, RECONNECT_MAX)


# ---------------------------------------------------------------------------
# FastAPI app (container health + metrics only - nothing public depends on this)
# ---------------------------------------------------------------------------


async def _publish_offline_on_shutdown() -> None:
    """Tell the broker we're going away, on a graceful stop as well as a crash.

    The Will only fires on an *unclean* disconnect; a graceful shutdown (SIGTERM, which
    is what ``docker compose stop`` sends) closes the MQTT connection properly, so
    nothing would publish "offline" at all without this - the aggregator would have to
    wait out the staleness timeout instead of finding out immediately. Best-effort: if
    the broker is already gone too, there's nothing more useful to do.
    """
    try:
        async with (
            asyncio.timeout(2),
            aiomqtt.Client(MQTT_HOST, MQTT_PORT, identifier=f"agent-{ROBOT_ID}-bye") as client,
        ):
            await client.publish(
                f"neurafleet/{ROBOT_ID}/status", _status_payload(False), qos=1, retain=True
            )
    except Exception as exc:
        logger.warning("%s: could not publish offline status on shutdown: %s", ROBOT_ID, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_mqtt_loop())
    _tasks.append(task)
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await _publish_offline_on_shutdown()


app = FastAPI(title=f"NeuraFleet Robot Agent ({ROBOT_ID})", version="1.0.0", lifespan=lifespan)
# The service label is this robot's own id (not a generic "robot_agent"): six containers
# run this same image, and /metrics + the JSON logs should read as that robot's, not
# merge into one indistinguishable series.
obs = install_observability(app, ROBOT_ID)
connected_gauge = obs.gauge("robot_agent_mqtt_connected", "1 if this agent's MQTT link is up")
connected_gauge.set_function(lambda: int(mqtt_connected))


@app.get("/health")
async def health():
    return {
        "status": "ok" if mqtt_connected else "degraded",
        "robot_id": ROBOT_ID,
        "mqtt": "connected" if mqtt_connected else "disconnected",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=False, log_level="info")
