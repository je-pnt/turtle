"""
Manifest Registry - Loads, registers, and publishes manifests.

ManifestRegistry is the single source of truth for all manifest definitions.
Manifests are published to the Metadata lane as ManifestPublished events,
creating a time-versioned record of UI schema definitions.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, List, TYPE_CHECKING
from datetime import datetime
import logging

from .base import Manifest, GenericManifest

if TYPE_CHECKING:
    from nova.core.database import Database

logger = logging.getLogger(__name__)


@dataclass
class ManifestRecord:
    """Internal record for a registered manifest with version history."""
    manifestId: str
    versions: Dict[int, Manifest] = field(default_factory=dict)
    currentVersion: int = 0
    publishedAt: Optional[datetime] = None
    
    def getVersion(self, version: Optional[int] = None) -> Optional[Manifest]:
        """Get manifest by version, or current if not specified."""
        v = version if version is not None else self.currentVersion
        return self.versions.get(v)
    
    def addVersion(self, manifest: Manifest) -> int:
        """Add a new version of the manifest."""
        self.currentVersion = manifest.manifestVersion
        self.versions[manifest.manifestVersion] = manifest
        return self.currentVersion


class ManifestRegistry:
    """
    Central registry for all UI manifests.
    
    Responsibilities:
    - Register and version manifests
    - Publish ManifestPublished events to Metadata lane
    - Lookup manifests by manifestId + version
    - Validate manifest data against schema
    """
    
    def __init__(self, database: Optional[Database] = None, scopeId: str = "local"):
        self._records: Dict[str, ManifestRecord] = {}
        self._byViewId: Dict[str, str] = {}  # viewId -> manifestId
        self._database = database
        self._scopeId = scopeId
        
    def register(self, manifest: Manifest, publish: bool = True) -> Manifest:
        """
        Register a manifest, optionally publishing to database.
        
        Args:
            manifest: The manifest to register
            publish: Whether to emit ManifestPublished event
            
        Returns:
            The registered manifest
        """
        manifestId = manifest.manifestId
        
        if manifestId not in self._records:
            self._records[manifestId] = ManifestRecord(manifestId=manifestId)
        
        record = self._records[manifestId]
        record.addVersion(manifest)
        
        # Track viewId -> manifestId mapping
        self._byViewId[manifest.viewId] = manifestId
        
        if publish and self._database:
            self._publishManifest(manifest)
            from datetime import timezone
            record.publishedAt = datetime.now(timezone.utc)
        
        logger.info(f"Registered manifest: {manifestId} v{manifest.manifestVersion}")
        return manifest
    
    def get(self, manifestId: str, version: Optional[int] = None) -> Optional[Manifest]:
        """
        Get a manifest by ID, optionally at a specific version.
        
        Args:
            manifestId: The manifest identifier
            version: Specific version, or None for current
            
        Returns:
            The manifest, or None if not found
        """
        record = self._records.get(manifestId)
        if not record:
            return None
        return record.getVersion(version)
    
    def getByViewId(self, viewId: str) -> Optional[Manifest]:
        """Get manifest by its viewId."""
        manifestId = self._byViewId.get(viewId)
        if not manifestId:
            return None
        return self.get(manifestId)
    
    def getAllManifests(self) -> List[Manifest]:
        """Get all current manifests."""
        return [
            record.getVersion() 
            for record in self._records.values()
            if record.getVersion() is not None
        ]
    
    def getManifestIds(self) -> List[str]:
        """Get all registered manifest IDs."""
        return list(self._records.keys())
    
    def _publishManifest(self, manifest: Manifest) -> None:
        """Emit ManifestPublished event to Metadata lane."""
        if not self._database:
            return
        
        from nova.core.events import MetadataEvent
        from datetime import timezone
        
        now = datetime.now(timezone.utc).isoformat()
        
        # ManifestPublished is a Metadata event that records the manifest definition
        # Uses manifestId identity (not entity triplet)
        event = MetadataEvent.create(
            scopeId=self._scopeId,
            sourceTruthTime=now,
            messageType="ManifestPublished",
            effectiveTime=now,
            manifestId=manifest.manifestId,
            payload={
                "manifestId": manifest.manifestId,
                "manifestVersion": manifest.manifestVersion,
                "viewId": manifest.viewId,
                "allowedKeys": list(manifest.getAllowedKeys().keys()),  # Just key names
                "fields": [f.toDict() for f in manifest.fields],
                "displayName": manifest.displayName,
                "description": manifest.description,
                "categories": manifest.categories
            }
        )
        
        # Insert with current time as canonicalTruthTime
        self._database.insertEvent(event, now)
        logger.debug(f"Published ManifestPublished: {manifest.manifestId} v{manifest.manifestVersion}")
    
    def loadBuiltinManifests(self) -> int:
        """
        Load built-in manifest definitions.
        
        Returns:
            Number of manifests loaded
        """
        from .telemetry import createTelemetryManifests
        
        count = 0
        for manifest in createTelemetryManifests():
            self.register(manifest, publish=True)
            count += 1
        
        logger.info(f"Loaded {count} built-in manifests")
        return count
    
    def validateData(self, manifestId: str, data: Dict, version: Optional[int] = None) -> List[str]:
        """
        Validate data against a manifest schema.
        
        Args:
            manifestId: The manifest to validate against
            data: The data to validate
            version: Specific manifest version, or None for current
            
        Returns:
            List of validation errors (empty if valid)
        """
        manifest = self.get(manifestId, version)
        if not manifest:
            return [f"Manifest not found: {manifestId}"]
        return manifest.validateData(data)


# Global registry instance (set during NOVA startup)
_registry: Optional[ManifestRegistry] = None


def getRegistry() -> Optional[ManifestRegistry]:
    """Get the global manifest registry."""
    return _registry


def setRegistry(registry: ManifestRegistry) -> None:
    """Set the global manifest registry."""
    global _registry
    _registry = registry
