"""
Run Manifest Registry - Plugin-style discovery for run types.

Architecture mirrors card manifests (cards.py):
- *.runManifest.py files in this directory export RUN_MANIFEST
- Discovered in sorted filename order for determinism
- Collision on runType = fail fast at startup
- generic.runManifest.py provides the base/default

Run manifests define:
- runType: identifier (e.g., "generic", "hardwareService")
- Schema: what fields this run type has
- UI: how to render the card (widgets, actions)
- Export: how to create bundles for this run type
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
from pathlib import Path
import importlib.util
import logging

logger = logging.getLogger(__name__)


class FieldType(str, Enum):
    """Field types for run manifest schema."""
    STRING = "string"           # Text input
    TEXT = "text"               # Multiline text
    NUMBER = "number"           # Numeric input
    DATETIME = "datetime"       # Datetime picker (UTC seconds)
    BOOLEAN = "boolean"         # Toggle/checkbox
    SELECT = "select"           # Dropdown from options
    ARRAY = "array"             # Array of sub-fields (e.g., musicTimes)
    OBJECT = "object"           # Nested object
    SIGNALS = "signals"         # Signal selection grid (special widget)


@dataclass
class RunField:
    """A single field in a run manifest schema."""
    fieldId: str                # Field key in run JSON (e.g., "analystNotes")
    label: str                  # Display label
    fieldType: FieldType        # Type determines widget
    required: bool = False      # Whether field is required
    default: Any = None         # Default value
    config: Dict[str, Any] = field(default_factory=dict)  # Type-specific config
    # For arrays: config.itemFields = [RunField, ...] defines the array item schema
    # For select: config.options = [{"value": ..., "label": ...}]
    # For signals: config.constellation = "GPS" or None for all


@dataclass
class RunManifest:
    """
    Manifest defining a run type's schema and UI.
    
    A run type is a category of replay/export manifest (e.g., generic, hardwareService).
    The manifest defines:
    - What fields exist and their types
    - How to render the run card
    - How to export/bundle this run type
    """
    runType: str                # Run type identifier (e.g., "generic")
    title: str                  # Display title (e.g., "Generic Run")
    icon: str                   # Emoji/icon
    color: str                  # Accent color (hex)
    description: str            # Help text
    
    # Schema - defines what fields this run type has
    # Core fields (name, startTimeSec, stopTimeSec, analystNotes) are always included
    fields: List[RunField] = field(default_factory=list)
    
    # Collapsible sections for UI organization
    sections: List[Dict[str, Any]] = field(default_factory=list)
    # e.g., [{"id": "times", "label": "Time Windows", "collapsed": False}, ...]
    
    # Export configuration
    exportEnabled: bool = True          # Whether bundles can be created
    exportHandler: Optional[str] = None # Custom export handler (module path)


# =============================================================================
# Run Manifest Registry - Deterministic Discovery
# =============================================================================

class RunManifestRegistry:
    """
    Registry for run manifests with deterministic file-based discovery.
    
    Discovery contract (mirrors CardRegistry):
    - Scans *.runManifest.py files in sorted filename order
    - Each file must export RUN_MANIFEST (RunManifest)
    - Collision on runType = fail fast
    - generic.runManifest.py provides fallback/default
    """
    
    def __init__(self):
        self._manifests: Dict[str, RunManifest] = {}      # runType → manifest
        self._defaultManifest: Optional[RunManifest] = None
    
    def discover(self, manifestDir: Optional[Path] = None) -> int:
        """
        Discover and load all *.runManifest.py files in sorted order.
        
        Args:
            manifestDir: Directory to scan (defaults to this module's directory)
            
        Returns:
            Number of manifests loaded
            
        Raises:
            RuntimeError: On duplicate runType collision
        """
        if manifestDir is None:
            manifestDir = Path(__file__).parent
        
        # Get manifest files in sorted order (determinism)
        manifestFiles = sorted(manifestDir.glob("*.runManifest.py"))
        
        for manifestPath in manifestFiles:
            moduleName = manifestPath.stem  # e.g., "generic.runManifest"
            
            try:
                # Import the manifest module
                spec = importlib.util.spec_from_file_location(moduleName, manifestPath)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # Get RUN_MANIFEST export
                if not hasattr(module, 'RUN_MANIFEST'):
                    logger.warning(f"[RunManifestRegistry] {manifestPath.name} missing RUN_MANIFEST export, skipping")
                    continue
                
                manifest = module.RUN_MANIFEST
                if not isinstance(manifest, RunManifest):
                    logger.warning(f"[RunManifestRegistry] {manifestPath.name} RUN_MANIFEST is not RunManifest, skipping")
                    continue
                
                self._registerManifest(manifest, manifestPath.name)
                
            except Exception as e:
                logger.error(f"[RunManifestRegistry] Failed to load {manifestPath.name}: {e}")
                raise
        
        logger.info(f"[RunManifestRegistry] Discovered {len(self._manifests)} run manifests")
        return len(self._manifests)
    
    def _registerManifest(self, manifest: RunManifest, sourceName: str) -> None:
        """Register a manifest, checking for collisions."""
        runType = manifest.runType
        
        if runType in self._manifests:
            raise RuntimeError(
                f"[RunManifestRegistry] Collision: runType '{runType}' already registered. "
                f"New definition from {sourceName}. Fail fast - fix manifest."
            )
        
        self._manifests[runType] = manifest
        
        # Track default/generic manifest
        if runType == "generic":
            self._defaultManifest = manifest
        
        logger.debug(f"[RunManifestRegistry] Registered runType: {runType}")
    
    def get(self, runType: str) -> Optional[RunManifest]:
        """Get manifest for run type."""
        return self._manifests.get(runType)
    
    def getOrDefault(self, runType: str) -> RunManifest:
        """Get manifest for run type, falling back to generic."""
        return self._manifests.get(runType) or self._defaultManifest
    
    def all(self) -> Dict[str, RunManifest]:
        """Get all registered manifests."""
        return dict(self._manifests)
    
    def listTypes(self) -> List[str]:
        """List all registered run types."""
        return list(self._manifests.keys())
    
    def toConfigDict(self) -> List[Dict[str, Any]]:
        """
        Convert all manifests to config dict for client.
        Used by server to send to UI via /config endpoint.
        """
        result = []
        for manifest in self._manifests.values():
            result.append({
                'runType': manifest.runType,
                'title': manifest.title,
                'icon': manifest.icon,
                'color': manifest.color,
                'description': manifest.description,
                'fields': [_fieldToDict(f) for f in manifest.fields],
                'sections': manifest.sections,
                'exportEnabled': manifest.exportEnabled
            })
        return result


def _fieldToDict(f: RunField) -> Dict[str, Any]:
    """Convert RunField to dict for JSON serialization."""
    d = {
        'fieldId': f.fieldId,
        'label': f.label,
        'fieldType': f.fieldType.value,
        'required': f.required,
        'config': f.config
    }
    if f.default is not None:
        d['default'] = f.default
    return d


# Global registry instance
_registry: Optional[RunManifestRegistry] = None


def getRunManifestRegistry() -> RunManifestRegistry:
    """Get the global run manifest registry, initializing if needed."""
    global _registry
    if _registry is None:
        _registry = RunManifestRegistry()
        _registry.discover()
    return _registry
