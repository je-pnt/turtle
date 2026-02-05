"""
NOVA Run Manifests

Run manifests define the schema and UI for different run types.
Plugin-style discovery: each *.runManifest.py exports RUN_MANIFEST.
"""

from .registry import RunManifest, RunField, FieldType, RunManifestRegistry, getRunManifestRegistry

__all__ = ['RunManifest', 'RunField', 'FieldType', 'RunManifestRegistry', 'getRunManifestRegistry']
