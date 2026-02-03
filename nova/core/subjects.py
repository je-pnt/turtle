"""
NOVA Transport Subject Naming

Public routing contract for NOVA truth events.
Deterministic subject formatting that works with or without SDK.

Canonical Subject Format: nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{schemaVersion}

Architecture Contract (nova architecture.md):
- Version last for multi-version subscription support
- Deterministic: same inputs → same subject
- Alphanumeric scopeId with hyphens: [A-Za-z0-9\\-]+
- URL-safe identity components: [A-Za-z0-9_\\-:.]+

Universal Identity Model:
  Public identity is ALWAYS: systemId + containerId + uniqueId
  This applies to ALL lanes - no per-lane identity differences.

  - systemId: The data system that produced the truth (e.g., hardwareService, nova)
  - containerId: The node/payload/site instance (e.g., node1, payloadA)
  - uniqueId: The renderable entity identifier within that system+container

Command lane specifics:
  - CommandRequest: systemId=nova (NOVA dispatches), uniqueId=commandId
  - CommandProgress/Result: systemId=producer (e.g., hardwareService), uniqueId=commandId
  - Routing selectors (targetId, commandType) stay in envelope, not subject
"""

import re
from typing import NamedTuple, Optional

from .contract import Lane


# Validation patterns
SCOPE_ID_PATTERN = re.compile(r'^[A-Za-z0-9\-]+$')
IDENTITY_COMPONENT_PATTERN = re.compile(r'^[A-Za-z0-9_\-:.]+$')


class RouteKey(NamedTuple):
    """
    Routing key for NOVA truth events.
    
    This is the public contract for addressing NOVA events.
    3rd parties can construct these without the SDK.
    
    Universal identity: systemId + containerId + uniqueId
    """
    scopeId: str
    lane: Lane
    systemId: str
    containerId: str
    uniqueId: str
    schemaVersion: int = 1


class SubjectError(Exception):
    """Subject formatting or validation error"""
    pass


def _validateComponent(name: str, value: str):
    """Validate a subject component (systemId, containerId, uniqueId)"""
    if not value:
        raise SubjectError(f"Empty {name}")
    if not IDENTITY_COMPONENT_PATTERN.match(value):
        raise SubjectError(
            f"Invalid {name} '{value}': must be URL-safe [A-Za-z0-9_\\-:.]+"
        )


def formatNovaSubject(routeKey: RouteKey) -> str:
    """
    Format NOVA transport subject (v1).
    
    Canonical subject format: nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{schemaVersion}
    
    This is a pure function implementing the public routing contract.
    3rd parties can implement the same formatter.
    
    Args:
        routeKey: Routing key with scope, lane, identity triplet, version
        
    Returns:
        Formatted subject string
        
    Raises:
        SubjectError: If validation fails
        
    Examples:
        >>> formatNovaSubject(RouteKey("payloadA", Lane.RAW, "hardwareService", "node1", "gps1", 1))
        'nova.payloadA.raw.hardwareService.node1.gps1.v1'
        
        >>> formatNovaSubject(RouteKey("ground", Lane.PARSED, "hardwareService", "node1", "streamGps", 1))
        'nova.ground.parsed.hardwareService.node1.streamGps.v1'
    """
    # Validate scopeId
    if not SCOPE_ID_PATTERN.match(routeKey.scopeId):
        raise SubjectError(
            f"Invalid scopeId '{routeKey.scopeId}': must be alphanumeric with hyphens [A-Za-z0-9\\-]+"
        )
    
    # Validate identity components
    _validateComponent("systemId", routeKey.systemId)
    _validateComponent("containerId", routeKey.containerId)
    _validateComponent("uniqueId", routeKey.uniqueId)
    
    # Validate schemaVersion
    if routeKey.schemaVersion < 1:
        raise SubjectError(
            f"Invalid schemaVersion {routeKey.schemaVersion}: must be >= 1"
        )
    
    # Format canonical subject
    return f"nova.{routeKey.scopeId}.{routeKey.lane.value}.{routeKey.systemId}.{routeKey.containerId}.{routeKey.uniqueId}.v{routeKey.schemaVersion}"


def parseNovaSubject(subject: str) -> RouteKey:
    """
    Parse NOVA transport subject back to RouteKey.
    
    Inverse of formatNovaSubject().
    
    Args:
        subject: NOVA subject string
        
    Returns:
        Parsed RouteKey
        
    Raises:
        SubjectError: If subject format invalid
        
    Examples:
        >>> parseNovaSubject('nova.payloadA.raw.hardwareService.node1.gps1.v1')
        RouteKey(scopeId='payloadA', lane=<Lane.RAW: 'raw'>, systemId='hardwareService', containerId='node1', uniqueId='gps1', schemaVersion=1)
    """
    parts = subject.split('.')
    
    # Canonical format: nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{version}
    # That's 7 parts minimum
    if len(parts) < 7:
        raise SubjectError(f"Invalid NOVA subject '{subject}': expected at least 7 parts (nova.scopeId.lane.systemId.containerId.uniqueId.vN)")
    
    if parts[0] != 'nova':
        raise SubjectError(f"Invalid NOVA subject '{subject}': must start with 'nova'")
    
    scopeId = parts[1]
    laneStr = parts[2]
    systemId = parts[3]
    containerId = parts[4]
    # uniqueId might contain dots, so join remaining parts except version
    uniqueId = '.'.join(parts[5:-1])
    versionStr = parts[-1]
    
    # Parse lane
    try:
        lane = Lane(laneStr)
    except ValueError:
        raise SubjectError(f"Invalid lane '{laneStr}': must be one of {[l.value for l in Lane]}")
    
    # Parse version
    if not versionStr.startswith('v'):
        raise SubjectError(f"Invalid version '{versionStr}': must start with 'v'")
    
    try:
        schemaVersion = int(versionStr[1:])
    except ValueError:
        raise SubjectError(f"Invalid version '{versionStr}': must be 'v' + integer")
    
    return RouteKey(
        scopeId=scopeId,
        lane=lane,
        systemId=systemId,
        containerId=containerId,
        uniqueId=uniqueId,
        schemaVersion=schemaVersion
    )


def formatSubscriptionPattern(
    scopeId: str = None, 
    lane: Lane = None, 
    systemId: str = None,
    containerId: str = None,
    schemaVersion: int = None
) -> str:
    """
    Format NOVA subscription pattern with wildcards.
    
    Canonical format: nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{version}
    
    Examples:
        >>> formatSubscriptionPattern()  # All NOVA events
        'nova.>'
        
        >>> formatSubscriptionPattern(scopeId="payloadA")  # All events for scope
        'nova.payloadA.>'
        
        >>> formatSubscriptionPattern(lane=Lane.RAW)  # All Raw events
        'nova.*.raw.>'
        
        >>> formatSubscriptionPattern(scopeId="payloadA", lane=Lane.RAW)  # Scope + lane
        'nova.payloadA.raw.>'
        
        >>> formatSubscriptionPattern(systemId="hardwareService")  # All from a system
        'nova.*.*.hardwareService.>'
    
    Args:
        scopeId: Filter by scope (None = all scopes)
        lane: Filter by lane (None = all lanes)
        systemId: Filter by systemId (None = all systems)
        containerId: Filter by containerId (None = all containers)
        schemaVersion: Filter by version (None = all versions)
        
    Returns:
        Subscription pattern with NATS wildcards
    """
    # Build pattern parts
    parts = ['nova']
    
    if scopeId is None and lane is None and systemId is None and containerId is None and schemaVersion is None:
        # Subscribe to everything under nova
        return 'nova.>'
    
    # ScopeId
    parts.append(scopeId if scopeId else '*')
    
    # Lane
    parts.append(lane.value if lane else '*')
    
    # If no identity filters, use hierarchical wildcard
    if systemId is None and containerId is None and schemaVersion is None:
        return '.'.join(parts) + '.>'
    
    # SystemId
    parts.append(systemId if systemId else '*')
    
    # ContainerId  
    parts.append(containerId if containerId else '*')
    
    # UniqueId (always wildcard in subscription patterns)
    parts.append('*')
    
    # SchemaVersion
    if schemaVersion:
        parts.append(f'v{schemaVersion}')
    else:
        parts.append('*')
    
    return '.'.join(parts)


def buildRouteKeyFromEvent(event: dict) -> RouteKey:
    """
    Build RouteKey from event envelope.
    
    Universal identity model: systemId + containerId + uniqueId for ALL lanes.
    No special cases - caller must provide the identity triplet.
    
    Args:
        event: Event envelope dict with scopeId, lane, systemId, containerId, uniqueId
        
    Returns:
        RouteKey for subject formatting
        
    Raises:
        SubjectError: If required fields missing
    """
    lane = Lane(event['lane']) if isinstance(event['lane'], str) else event['lane']
    scopeId = event.get('scopeId')
    systemId = event.get('systemId')
    containerId = event.get('containerId')
    uniqueId = event.get('uniqueId')
    schemaVersion = event.get('schemaVersion', 1)
    
    # Validate required fields - no special cases, no fallbacks
    if not scopeId:
        raise SubjectError("Event missing scopeId")
    if not systemId:
        raise SubjectError("Event missing systemId")
    if not containerId:
        raise SubjectError("Event missing containerId")
    if not uniqueId:
        raise SubjectError("Event missing uniqueId")
    
    return RouteKey(
        scopeId=scopeId,
        lane=lane,
        systemId=systemId,
        containerId=containerId,
        uniqueId=uniqueId,
        schemaVersion=schemaVersion
    )
