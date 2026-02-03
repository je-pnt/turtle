import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from sdk.transport import createTransport
from nova.core.events import UiUpdate, MetadataEvent, Lane
from nova.core.subjects import RouteKey, formatNovaSubject


TRANSPORT_URI = "nats://localhost:4222"
SCOPE_ID = "payload-local"
SYSTEM_ID = "orbitSim"
CONTAINER_ID = "test"
SCHEMA_VERSION = 1

ORBIT_CENTER = {"lat": 37.7749, "lon": -122.4194}
UPDATE_HZ = 2.0

ENTITIES = [
    {
        "uniqueId": "orbiter-1km",
        "displayName": "Orbiter 1km",
        "entityType": "orbiter",
        "modelRef": "Falcon.glb",
        "color": [0, 212, 255],
        "scale": 1.0,
        "radius_m": 1000.0,
        "speed_mps": 50.0,
        "alt_m": 2000.0,
        "phase_deg": 0.0,
    },
    {
        "uniqueId": "orbiter-3km",
        "displayName": "Orbiter 3km",
        "entityType": "orbiter",
        "modelRef": "Falcon.glb",
        "color": [255, 140, 0],
        "scale": 1.2,
        "radius_m": 3000.0,
        "speed_mps": 50.0,
        "alt_m": 2000.0,
        "phase_deg": 180.0,
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_subject(lane: Lane, unique_id: str) -> str:
    return formatNovaSubject(
        RouteKey(
            scopeId=SCOPE_ID,
            lane=lane,
            systemId=SYSTEM_ID,
            containerId=CONTAINER_ID,
            uniqueId=unique_id,
            schemaVersion=SCHEMA_VERSION,
        )
    )


def add_schema_version(envelope: dict) -> dict:
    envelope["schemaVersion"] = SCHEMA_VERSION
    return envelope


def compute_orbit_position(entity: dict, t_sec: float) -> tuple[float, float]:
    earth_radius_m = 6378137.0
    lat0 = math.radians(ORBIT_CENTER["lat"])
    lon0 = math.radians(ORBIT_CENTER["lon"])

    radius = entity["radius_m"]
    omega = entity["speed_mps"] / radius
    angle = omega * t_sec + math.radians(entity["phase_deg"])

    east = radius * math.cos(angle)
    north = radius * math.sin(angle)

    lat = lat0 + (north / earth_radius_m)
    lon = lon0 + (east / (earth_radius_m * math.cos(lat0)))

    return math.degrees(lat), math.degrees(lon)


async def publish_event(transport, subject: str, envelope: dict) -> None:
    payload = json.dumps(envelope).encode("utf-8")
    await transport.publish(subject, payload)


async def publish_metadata(transport) -> None:
    for entity in ENTITIES:
        ts = now_iso()
        payload = {
            "displayName": entity["displayName"],
            "entityType": entity["entityType"],
            "modelRef": entity["modelRef"],
            "color": entity["color"],
            "scale": entity["scale"],
        }
        event = MetadataEvent.create(
            scopeId=SCOPE_ID,
            sourceTruthTime=ts,
            messageType="ProducerDescriptor",
            effectiveTime=ts,
            payload=payload,
            systemId=SYSTEM_ID,
            containerId=CONTAINER_ID,
            uniqueId=entity["uniqueId"],
        )
        envelope = add_schema_version(event.toDict())
        subject = make_subject(Lane.METADATA, entity["uniqueId"])
        await publish_event(transport, subject, envelope)


async def publish_positions(transport) -> None:
    start = time.monotonic()
    interval = 1.0 / UPDATE_HZ
    while True:
        t_sec = time.monotonic() - start
        for entity in ENTITIES:
            lat, lon = compute_orbit_position(entity, t_sec)
            ts = now_iso()
            data = {
                "lat": lat,
                "lon": lon,
                "alt": entity["alt_m"],
            }
            event = UiUpdate.create(
                scopeId=SCOPE_ID,
                sourceTruthTime=ts,
                systemId=SYSTEM_ID,
                containerId=CONTAINER_ID,
                uniqueId=entity["uniqueId"],
                viewId="telemetry.gnss",
                manifestId="telemetry.gnss",
                manifestVersion="1.0.0",
                data=data,
                messageType="UiUpdate",
            )
            envelope = add_schema_version(event.toDict())
            subject = make_subject(Lane.UI, entity["uniqueId"])
            await publish_event(transport, subject, envelope)
        await asyncio.sleep(interval)


async def main() -> None:
    transport = createTransport(TRANSPORT_URI)
    await transport.connect(TRANSPORT_URI)
    await publish_metadata(transport)
    await publish_positions(transport)


if __name__ == "__main__":
    asyncio.run(main())
