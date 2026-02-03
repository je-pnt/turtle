"""
NOVA Telemetry Manifests

Defines the built-in manifests for telemetry data views.
Each manifest declares allowed keys, types, and display hints.

Architecture (nova architecture.md):
- Manifests are NOVA-owned
- UI meaning defined through manifests
- UiUpdate/UiCheckpoint must reference valid manifest

Design (guidelines.md):
- Explicit, deterministic definitions
- Small well-named abstractions
"""

from dataclasses import dataclass, field
from typing import List

from .base import Manifest, FieldDef, FieldType


@dataclass
class GnssManifest(Manifest):
    """Manifest for GNSS/GPS position data."""
    manifestId: str = "telemetry.gnss"
    manifestVersion: str = "1.0.0"
    viewId: str = "telemetry.gnss"
    displayName: str = "GNSS Position"
    description: str = "GNSS/GPS position and timing data"
    categories: List[str] = field(default_factory=lambda: ["position", "timing", "quality"])
    fields: List[FieldDef] = field(default_factory=lambda: [
        # Position category
        FieldDef("lat", FieldType.NUMBER, "Latitude", unit="deg", precision=6, required=False, category="position"),
        FieldDef("lon", FieldType.NUMBER, "Longitude", unit="deg", precision=6, required=False, category="position"),
        FieldDef("alt", FieldType.NUMBER, "Altitude", unit="m", precision=1, required=False, category="position"),
        FieldDef("position", FieldType.POSITION, "Position", required=False, category="position"),
        # Timing category
        FieldDef("gnssTime", FieldType.TIMESTAMP, "GNSS Time", required=False, category="timing"),
        FieldDef("itow", FieldType.NUMBER, "Time of Week", unit="ms", precision=0, required=False, category="timing"),
        # Quality category
        FieldDef("fixType", FieldType.NUMBER, "Fix Type", required=False, category="quality"),
        FieldDef("numSv", FieldType.NUMBER, "Satellites", required=False, category="quality"),
        FieldDef("pDOP", FieldType.NUMBER, "PDOP", precision=2, required=False, category="quality"),
        FieldDef("hAcc", FieldType.NUMBER, "Horiz. Accuracy", unit="m", precision=2, required=False, category="quality"),
        FieldDef("vAcc", FieldType.NUMBER, "Vert. Accuracy", unit="m", precision=2, required=False, category="quality"),
    ])


@dataclass
class VelocityManifest(Manifest):
    """Manifest for velocity data."""
    manifestId: str = "telemetry.velocity"
    manifestVersion: str = "1.0.0"
    viewId: str = "telemetry.velocity"
    displayName: str = "Velocity"
    description: str = "Velocity and heading data"
    categories: List[str] = field(default_factory=lambda: ["velocity", "heading"])
    fields: List[FieldDef] = field(default_factory=lambda: [
        # Velocity category
        FieldDef("velN", FieldType.NUMBER, "Velocity N", unit="m/s", precision=2, required=False, category="velocity"),
        FieldDef("velE", FieldType.NUMBER, "Velocity E", unit="m/s", precision=2, required=False, category="velocity"),
        FieldDef("velD", FieldType.NUMBER, "Velocity D", unit="m/s", precision=2, required=False, category="velocity"),
        FieldDef("gSpeed", FieldType.NUMBER, "Ground Speed", unit="m/s", precision=2, required=False, category="velocity"),
        # Heading category
        FieldDef("headMot", FieldType.NUMBER, "Heading (Motion)", unit="deg", precision=1, required=False, category="heading"),
        FieldDef("headVeh", FieldType.NUMBER, "Heading (Vehicle)", unit="deg", precision=1, required=False, category="heading"),
    ])


@dataclass
class EntityStatusManifest(Manifest):
    """Manifest for entity status and identity."""
    manifestId: str = "entity.status"
    manifestVersion: str = "1.0.0"
    viewId: str = "entity.status"
    displayName: str = "Entity Status"
    description: str = "Entity identity and status fields"
    categories: List[str] = field(default_factory=lambda: ["identity", "status"])
    fields: List[FieldDef] = field(default_factory=lambda: [
        # Identity category
        FieldDef("displayName", FieldType.STRING, "Display Name", required=False, category="identity"),
        FieldDef("entityType", FieldType.STRING, "Entity Type", required=False, category="identity"),
        FieldDef("description", FieldType.STRING, "Description", required=False, category="identity"),
        # Status category (computed by UI, but can be hinted)
        FieldDef("lastSeenTime", FieldType.TIMESTAMP, "Last Seen", required=False, category="status"),
        FieldDef("statusColor", FieldType.STRING, "Status Color", required=False, category="status"),
        FieldDef("statusText", FieldType.STRING, "Status Text", required=False, category="status"),
    ])


@dataclass
class ShieldManifest(Manifest):
    """Manifest for shield icon/appearance."""
    manifestId: str = "ui.shield"
    manifestVersion: str = "1.0.0"
    viewId: str = "ui.shield"
    displayName: str = "Shield"
    description: str = "Shield display properties"
    categories: List[str] = field(default_factory=lambda: ["appearance", "badge"])
    fields: List[FieldDef] = field(default_factory=lambda: [
        # Appearance category
        FieldDef("icon", FieldType.STRING, "Icon", required=False, category="appearance"),
        FieldDef("color", FieldType.STRING, "Color", required=False, category="appearance"),
        FieldDef("modelRef", FieldType.STRING, "Model Reference", required=False, category="appearance"),
        # Badge category (count/notification)
        FieldDef("badgeCount", FieldType.NUMBER, "Badge Count", required=False, category="badge"),
        FieldDef("badgeColor", FieldType.STRING, "Badge Color", required=False, category="badge"),
    ])


@dataclass
class MapViewManifest(Manifest):
    """Manifest for map display data."""
    manifestId: str = "ui.map"
    manifestVersion: str = "1.0.0"
    viewId: str = "ui.map"
    displayName: str = "Map View"
    description: str = "Map display properties"
    categories: List[str] = field(default_factory=lambda: ["position", "track", "appearance"])
    fields: List[FieldDef] = field(default_factory=lambda: [
        # Position category
        FieldDef("position", FieldType.POSITION, "Position", required=False, category="position"),
        FieldDef("heading", FieldType.NUMBER, "Heading", unit="deg", precision=1, required=False, category="position"),
        # Track category
        FieldDef("trackEnabled", FieldType.BOOLEAN, "Show Track", required=False, category="track"),
        FieldDef("trackColor", FieldType.STRING, "Track Color", required=False, category="track"),
        FieldDef("trackLength", FieldType.NUMBER, "Track Length", unit="pts", required=False, category="track"),
        # Appearance category
        FieldDef("modelRef", FieldType.STRING, "3D Model", required=False, category="appearance"),
        FieldDef("iconUrl", FieldType.STRING, "Icon URL", required=False, category="appearance"),
        FieldDef("scale", FieldType.NUMBER, "Scale", precision=2, required=False, category="appearance"),
    ])


def createTelemetryManifests() -> List[Manifest]:
    """
    Create and return all built-in telemetry manifests.
    
    Returns:
        List of manifest instances to be registered
    """
    return [
        GnssManifest(),
        VelocityManifest(),
        EntityStatusManifest(),
        ShieldManifest(),
        MapViewManifest(),
    ]
