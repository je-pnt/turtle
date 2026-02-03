"""
NOVA Architectural Contract Definitions

SINGLE SOURCE OF TRUTH for all architectural invariants.
These definitions implement the fixed contracts from nova architecture.md.

DO NOT duplicate these definitions elsewhere in the codebase.
Import from this module to ensure consistency.

Architecture Source: nova architecture.md sections 5.2 (Lane Model) and 5.3 (Ordering)

Identity Model (nova architecture.md Section 3):
  Public/external identity is always: scopeId + lane + systemId + containerId + uniqueId
  - systemId: The data system that produced the truth (e.g., hardwareService, adsb)
  - containerId: The node/payload/site instance (e.g., node1, payloadA, truck7)
  - uniqueId: The entity identifier within that system+container (deviceId/taskId/etc)
  
  Optional debug fields (not primary identity):
  - connectionId: Raw-byte source identity (TCP/serial/etc). Optional debug label.
  - streamId: Semantic typed stream identity. Optional technical label.
"""

from typing import Dict, List

# Import type definitions from events.py (enums are type definitions, not constants)
from .events import Lane, Timebase


# ============================================================================
# Entity Identity (Section 3 - System Model)
# ============================================================================

# Universal entity identity fields for ALL lanes
ENTITY_IDENTITY_FIELDS: List[str] = ["systemId", "containerId", "uniqueId"]

# EntityIdentityKey for EventId hash construction: systemId|containerId|uniqueId
def buildEntityIdentityKey(systemId: str, containerId: str, uniqueId: str) -> str:
    """Build the entity identity key for EventId hash construction."""
    return f"{systemId}|{containerId}|{uniqueId}"


# ============================================================================
# Ordering Contract (Section 5.3)
# ============================================================================

# Lane priority for ordering tie-breaks (lower number = higher priority)
# Metadata → Command → UI → Parsed → Raw
LANE_PRIORITY: Dict[Lane, int] = {
    Lane.METADATA: 0,
    Lane.COMMAND: 1,
    Lane.UI: 2,
    Lane.PARSED: 3,
    Lane.RAW: 4
}


# ============================================================================
# Database Schema Mapping
# ============================================================================

# Lane to database table name mapping
LANE_TABLE_NAMES: Dict[Lane, str] = {
    Lane.RAW: "rawEvents",
    Lane.PARSED: "parsedEvents",
    Lane.UI: "uiEvents",
    Lane.COMMAND: "commandEvents",
    Lane.METADATA: "metadataEvents"
}

# Note: Explicit CREATE TABLE schemas remain in database.py to prevent schema creep.
# We only centralize table name constants here, not column definitions.


# ============================================================================
# Validation Helpers
# ============================================================================

def validateLanePriority():
    """
    Validate that LANE_PRIORITY covers all lanes with unique priorities.
    Called at module import to catch contract violations early.
    """
    assert set(LANE_PRIORITY.keys()) == set(Lane), \
        "LANE_PRIORITY must cover all lanes"
    
    priorities = list(LANE_PRIORITY.values())
    assert len(priorities) == len(set(priorities)), \
        "LANE_PRIORITY must have unique priority values"
    
    assert priorities == sorted(priorities), \
        "LANE_PRIORITY values should be sequential starting from 0"


def validateTableMapping():
    """
    Validate that LANE_TABLE_NAMES covers all lanes.
    Called at module import to catch contract violations early.
    """
    assert set(LANE_TABLE_NAMES.keys()) == set(Lane), \
        "LANE_TABLE_NAMES must cover all lanes"


# Run validations at module import to fail fast on contract violations
validateLanePriority()
validateTableMapping()
