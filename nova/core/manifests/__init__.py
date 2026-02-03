"""
NOVA Manifest System

Manifests are NOVA-owned definitions that control UI semantics:
- viewId: unique view identifier
- allowedKeys: dict of key → type/validation
- layout: optional card/shield arrangement hints

Architecture (nova architecture.md):
- UI meaning is NOVA-owned and manifest-defined
- UiUpdate/UiCheckpoint reference ManifestId+ManifestVersion
- ManifestPublished metadata events record manifest versions as truth
"""

from .base import Manifest, FieldDef, FieldType, GenericManifest
from .registry import ManifestRegistry, ManifestRecord, getRegistry, setRegistry
from .telemetry import (
    GnssManifest,
    VelocityManifest,
    EntityStatusManifest,
    ShieldManifest,
    MapViewManifest,
    createTelemetryManifests
)

__all__ = [
    # Base
    'Manifest', 
    'FieldDef', 
    'FieldType', 
    'GenericManifest',
    # Registry
    'ManifestRegistry', 
    'ManifestRecord',
    'getRegistry', 
    'setRegistry',
    # Telemetry manifests
    'GnssManifest',
    'VelocityManifest',
    'EntityStatusManifest',
    'ShieldManifest',
    'MapViewManifest',
    'createTelemetryManifests',
]
