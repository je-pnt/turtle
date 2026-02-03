"""
NOVA Card Manifests

Card manifests define the UI layout for entity cards:
- cardType: the card identifier (e.g., "gnss-receiver-card")
- title, icon, color: display properties
- widgets: data-bound display elements
- actions: command buttons

Architecture (nova architecture.md):
- Cards are manifest-driven, not hardcoded
- Entity type maps to card type
- Actions trigger CommandRequest events

Phase 8 Contract:
- Manifests discovered from *.manifest.py files in sorted filename order
- Each file exports MANIFEST (a CardManifest)
- Collision on entityType = fail fast at startup
- Default manifest used when no match

Design (guidelines.md):
- Explicit, deterministic definitions
- Single rendering path
- No legacy/parallel code paths
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
from pathlib import Path
import importlib
import importlib.util
import logging

logger = logging.getLogger(__name__)


class WidgetType(str, Enum):
    """Widget types for card rendering."""
    TEXT = "text"           # Simple text display
    NUMBER = "number"       # Formatted number with unit
    STATUS = "status"       # Status indicator (fix type, online, etc.)
    POSITION = "position"   # Lat/lon/alt combined
    TABLE = "table"         # Tabular data (key-value rows)
    PROGRESS = "progress"   # Progress bar
    TIMESTAMP = "timestamp" # UTC timestamp matching timeline format
    SV_TABLE = "svTable"    # Satellite/signal table (nav_sat, nav_sig)
    SELECT = "select"       # Dropdown select
    TOGGLE = "toggle"       # Boolean toggle


@dataclass
class Widget:
    """A single data-bound widget in a card."""
    widgetType: WidgetType
    binding: str            # Data key to bind (e.g., "lat", "fixType")
    label: str              # Display label
    config: Dict[str, Any] = field(default_factory=dict)  # Type-specific config


@dataclass
class Action:
    """A command action button on a card."""
    actionId: str           # Unique action identifier
    label: str              # Button label
    commandType: str        # Command type to send (e.g., "coldReset")
    icon: Optional[str] = None
    confirm: bool = False   # Require confirmation dialog


@dataclass 
class CardManifest:
    """Manifest defining a card's layout and behavior."""
    cardType: str           # Card type identifier
    title: str              # Card title
    icon: str               # Icon name (emoji or icon class)
    color: str              # Accent color (hex)
    onlineIndicator: bool   # Show online/offline status
    widgets: List[Widget]   # Data widgets
    actions: List[Action]   # Command actions
    entityTypes: List[str]  # Entity types this card applies to


# =============================================================================
# Card Registry - Deterministic Discovery
# =============================================================================

class CardRegistry:
    """
    Registry for card manifests with deterministic file-based discovery.
    
    Discovery contract (Phase 8):
    - Scans *.manifest.py files in sorted filename order
    - Each file must export MANIFEST (CardManifest)
    - Collision on entityType = fail fast
    - default.manifest.py provides fallback
    """
    
    def __init__(self):
        self._manifests: Dict[str, CardManifest] = {}      # cardType → manifest
        self._entityTypeToCard: Dict[str, str] = {}        # entityType → cardType
        self._defaultManifest: Optional[CardManifest] = None
    
    def discover(self, manifestDir: Optional[Path] = None) -> int:
        """
        Discover and load all *.manifest.py files in sorted order.
        
        Args:
            manifestDir: Directory to scan (defaults to this module's directory)
            
        Returns:
            Number of manifests loaded
            
        Raises:
            RuntimeError: On duplicate entityType collision
        """
        if manifestDir is None:
            manifestDir = Path(__file__).parent
        
        # Get manifest files in sorted order (determinism)
        manifestFiles = sorted(manifestDir.glob("*.manifest.py"))
        
        for manifestPath in manifestFiles:
            moduleName = manifestPath.stem  # e.g., "gnssReceiver.manifest"
            
            try:
                # Import the manifest module
                spec = importlib.util.spec_from_file_location(moduleName, manifestPath)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # Get MANIFEST export
                if not hasattr(module, 'MANIFEST'):
                    logger.warning(f"[CardRegistry] {manifestPath.name} missing MANIFEST export, skipping")
                    continue
                
                manifest = module.MANIFEST
                if not isinstance(manifest, CardManifest):
                    logger.warning(f"[CardRegistry] {manifestPath.name} MANIFEST is not CardManifest, skipping")
                    continue
                
                self._registerManifest(manifest, manifestPath.name)
                
            except Exception as e:
                logger.error(f"[CardRegistry] Failed to load {manifestPath.name}: {e}")
                raise
        
        logger.info(f"[CardRegistry] Discovered {len(self._manifests)} card manifests")
        return len(self._manifests)
    
    def _registerManifest(self, manifest: CardManifest, sourceName: str) -> None:
        """Register a manifest, checking for collisions."""
        # Store by cardType
        self._manifests[manifest.cardType] = manifest
        
        # Handle default manifest
        if manifest.cardType == "default-card":
            self._defaultManifest = manifest
            return
        
        # Register entityType mappings with collision detection
        for entityType in manifest.entityTypes:
            if entityType in self._entityTypeToCard:
                existingCard = self._entityTypeToCard[entityType]
                raise RuntimeError(
                    f"[CardRegistry] Collision: entityType '{entityType}' claimed by both "
                    f"'{existingCard}' and '{manifest.cardType}' (from {sourceName}). "
                    "Fail fast - fix manifest entityTypes."
                )
            self._entityTypeToCard[entityType] = manifest.cardType
        
        logger.debug(f"[CardRegistry] Registered {manifest.cardType} for entityTypes: {manifest.entityTypes}")
    
    def getCardForEntityType(self, entityType: str) -> CardManifest:
        """Get card manifest for entity type, falling back to default."""
        cardType = self._entityTypeToCard.get(entityType)
        if cardType:
            return self._manifests[cardType]
        return self._defaultManifest or self._manifests.get("default-card")
    
    def getManifest(self, cardType: str) -> Optional[CardManifest]:
        """Get manifest by card type."""
        return self._manifests.get(cardType)
    
    def getAllManifests(self) -> List[CardManifest]:
        """Get all registered manifests."""
        return list(self._manifests.values())
    
    def getEntityTypeMap(self) -> Dict[str, str]:
        """Get entityType → cardType mapping for client."""
        return dict(self._entityTypeToCard)


# Global registry instance
_cardRegistry: Optional[CardRegistry] = None


def getCardRegistry() -> CardRegistry:
    """Get or create the global card registry."""
    global _cardRegistry
    if _cardRegistry is None:
        _cardRegistry = CardRegistry()
        _cardRegistry.discover()
    return _cardRegistry


def cardManifestToDict(card: CardManifest) -> Dict[str, Any]:
    """Convert a CardManifest to a dictionary for JSON serialization."""
    return {
        "cardType": card.cardType,
        "title": card.title,
        "icon": card.icon,
        "color": card.color,
        "onlineIndicator": card.onlineIndicator,
        "entityTypes": card.entityTypes,
        "widgets": [
            {
                "widgetType": w.widgetType.value,
                "binding": w.binding,
                "label": w.label,
                "config": w.config
            }
            for w in card.widgets
        ],
        "actions": [
            {
                "actionId": a.actionId,
                "label": a.label,
                "commandType": a.commandType,
                "icon": a.icon,
                "confirm": a.confirm
            }
            for a in card.actions
        ]
    }


def getAllCardManifestsDict() -> List[Dict[str, Any]]:
    """Return all card manifests as dictionaries for client consumption."""
    registry = getCardRegistry()
    return [cardManifestToDict(c) for c in registry.getAllManifests()]


# Legacy compatibility - will be removed after validation
def getCardForEntityType(entityType: str) -> CardManifest:
    """Get card manifest for entity type (uses registry)."""
    return getCardRegistry().getCardForEntityType(entityType)

