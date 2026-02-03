"""
NOVA Transport Manager

Manages transport subscription for Core process.
Subscribes to NOVA truth events via sdk.transport and forwards to ingest.

Architecture Contract:
- Core subscribes via scopeId filter (payload mode) or all scopes (ground mode)
- Transport subject pattern: nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{schemaVersion}
- Validates envelope structure before forwarding to ingest
- Logs transport/address mismatches but prefers envelope fields
"""

import asyncio
import json
from typing import Optional, Callable
from datetime import datetime

from .subjects import formatSubscriptionPattern, parseNovaSubject, RouteKey
from .ingest import Ingest
from .events import RawFrame, ParsedMessage, UiUpdate, CommandRequest, MetadataEvent, Lane
from sdk.logging import getLogger


class TransportError(Exception):
    """Transport subscription or handling error"""
    pass


class TransportManager:
    """
    Transport subscription manager for NOVA Core.
    
    Subscribes to NOVA truth events and forwards validated envelopes to ingest.
    """
    
    def __init__(self, ingest: Ingest, transport, scopeId: Optional[str] = None):
        """
        Initialize transport manager.
        
        Args:
            ingest: Ingest instance to forward events to
            transport: sdk.transport instance (already connected)
            scopeId: Scope to subscribe to (None = all scopes for ground mode)
        """
        self.ingest = ingest
        self.transport = transport
        self.scopeId = scopeId
        self.subscriptions = []
        self._running = False
        self.log = getLogger()
        self.ingestCount = 0
        
    async def start(self):
        """
        Start transport subscription.
        
        Subscribes to NOVA events based on scopeId filter:
        - Payload mode: subscribe to own scopeId only
        - Ground mode: subscribe to all scopes
        """
        if self._running:
            raise TransportError("TransportManager already running")
        
        # Build subscription pattern
        pattern = formatSubscriptionPattern(scopeId=self.scopeId)
        
        self.log.info(f"[TransportManager] Subscribing to: {pattern}")
        
        # Subscribe via sdk.transport
        subscription = await self.transport.subscribe(
            subject=pattern,
            handler=self._handleMessage
        )
        
        self.subscriptions.append(subscription)
        self._running = True
        
        self.log.info(f"[TransportManager] Subscription active: {pattern}")
        
        print(f"[TransportManager] Subscription active: {pattern}")
    
    async def stop(self):
        """Stop transport subscription and clean up."""
        if not self._running:
            return
        
        print("[TransportManager] Stopping subscriptions...")
        
        # Unsubscribe all
        for sub in self.subscriptions:
            await sub.unsubscribe()
        
        self.subscriptions.clear()
        self._running = False
        
        print("[TransportManager] Stopped")
    
    async def _handleMessage(self, subject: str, payload: bytes):
        """
        Handle incoming transport message.
        
        Validates envelope structure and forwards to ingest.
        
        Args:
            subject: Transport subject (e.g., nova.payloadA.raw.conn1.v1)
            payload: Message payload bytes (JSON for most lanes, may be raw bytes)
        """
        try:
            # Parse subject for routing info
            try:
                routeKey = parseNovaSubject(subject)
            except Exception as e:
                print(f"[TransportManager] Invalid subject '{subject}': {e}")
                return
            
            # Decode payload
            try:
                payloadStr = payload.decode('utf-8')
                envelope = json.loads(payloadStr)
            except Exception as e:
                print(f"[TransportManager] Invalid JSON payload on '{subject}': {e}")
                return
            
            # Validate required envelope fields
            if not self._validateEnvelope(envelope):
                print(f"[TransportManager] Invalid envelope on '{subject}': missing required fields")
                return
            
            # Check for address/envelope mismatch (log but don't drop)
            self._checkMismatch(routeKey, envelope, subject)
            
            # Convert envelope to Event object
            event = self._envelopeToEvent(envelope)
            if event is None:
                print(f"[TransportManager] Failed to convert envelope on '{subject}'")
                return
            
            # Forward to ingest (sync call in async context)
            # Note: Ingest.ingest() is synchronous in Phase 1
            success = self.ingest.ingest(event)
            
            # Reduce logging noise - only log periodically
            self.ingestCount += 1
            if self.ingestCount % 1000 == 0:
                self.log.info(f"[TransportManager] Ingested {self.ingestCount} events total")
        
        except Exception as e:
            print(f"[TransportManager] Error handling message on '{subject}': {e}")
            import traceback
            traceback.print_exc()
    
    def _validateEnvelope(self, envelope: dict) -> bool:
        """
        Validate required envelope fields.
        
        Required for all envelopes:
        - schemaVersion, eventId, scopeId, lane, sourceTruthTime
        
        Returns:
            True if valid, False otherwise
        """
        required = ['schemaVersion', 'eventId', 'scopeId', 'lane', 'sourceTruthTime']
        return all(field in envelope for field in required)
    
    def _checkMismatch(self, routeKey: RouteKey, envelope: dict, subject: str):
        """
        Check for routing/envelope mismatches.
        
        Logs warnings but doesn't drop data (prefer envelope fields per architecture).
        
        Args:
            routeKey: Parsed from transport subject
            envelope: Message envelope
            subject: Original subject string
        """
        # Check scopeId mismatch
        if routeKey.scopeId != envelope.get('scopeId'):
            print(f"[TransportManager] WARNING: scopeId mismatch on '{subject}'")
            print(f"  Subject: {routeKey.scopeId}, Envelope: {envelope.get('scopeId')}")
        
        # Check lane mismatch
        if routeKey.lane.value != envelope.get('lane'):
            print(f"[TransportManager] WARNING: lane mismatch on '{subject}'")
            print(f"  Subject: {routeKey.lane.value}, Envelope: {envelope.get('lane')}")
        
        # Check schemaVersion mismatch
        if routeKey.schemaVersion != envelope.get('schemaVersion'):
            print(f"[TransportManager] WARNING: schemaVersion mismatch on '{subject}'")
            print(f"  Subject: {routeKey.schemaVersion}, Envelope: {envelope.get('schemaVersion')}")
    
    def _envelopeToEvent(self, envelope: dict):
        """
        Convert envelope dict to Event object.
        
        Uses new identity model: systemId + containerId + uniqueId for ALL lanes.
        
        Args:
            envelope: Validated envelope dict
            
        Returns:
            Event object or None if conversion fails
        """
        lane = envelope['lane']
        
        try:
            if lane == 'raw':
                return RawFrame(
                    eventId=envelope['eventId'],
                    scopeId=envelope['scopeId'],
                    sourceTruthTime=envelope['sourceTruthTime'],
                    systemId=envelope['systemId'],
                    containerId=envelope['containerId'],
                    uniqueId=envelope['uniqueId'],
                    bytesData=bytes.fromhex(envelope['bytes']) if isinstance(envelope['bytes'], str) else envelope['bytes'],
                    connectionId=envelope.get('connectionId'),  # Optional debug
                    sequence=envelope.get('sequence')  # Optional debug
                )
            
            elif lane == 'parsed':
                return ParsedMessage(
                    eventId=envelope['eventId'],
                    scopeId=envelope['scopeId'],
                    sourceTruthTime=envelope['sourceTruthTime'],
                    systemId=envelope['systemId'],
                    containerId=envelope['containerId'],
                    uniqueId=envelope['uniqueId'],
                    messageType=envelope['messageType'],
                    schemaVersion=envelope.get('schemaVersion', '1'),
                    payload=envelope['payload']
                )
            
            elif lane == 'ui':
                return UiUpdate(
                    eventId=envelope['eventId'],
                    scopeId=envelope['scopeId'],
                    sourceTruthTime=envelope['sourceTruthTime'],
                    messageType=envelope.get('messageType', 'UiUpdate'),
                    systemId=envelope['systemId'],
                    containerId=envelope['containerId'],
                    uniqueId=envelope['uniqueId'],
                    viewId=envelope['viewId'],
                    manifestId=envelope['manifestId'],
                    manifestVersion=envelope['manifestVersion'],
                    data=envelope['data']
                )
            
            elif lane == 'command':
                return CommandRequest(
                    eventId=envelope['eventId'],
                    scopeId=envelope['scopeId'],
                    sourceTruthTime=envelope['sourceTruthTime'],
                    messageType=envelope.get('messageType', 'CommandRequest'),
                    systemId=envelope['systemId'],
                    containerId=envelope['containerId'],
                    uniqueId=envelope['uniqueId'],
                    commandId=envelope['commandId'],
                    requestId=envelope.get('requestId'),
                    targetId=envelope['targetId'],
                    commandType=envelope['commandType'],
                    payload=envelope['payload']
                )
            
            elif lane == 'metadata':
                return MetadataEvent(
                    eventId=envelope['eventId'],
                    scopeId=envelope['scopeId'],
                    sourceTruthTime=envelope['sourceTruthTime'],
                    messageType=envelope['messageType'],
                    effectiveTime=envelope['effectiveTime'],
                    systemId=envelope.get('systemId'),
                    containerId=envelope.get('containerId'),
                    uniqueId=envelope.get('uniqueId'),
                    manifestId=envelope.get('manifestId'),
                    payload=envelope['payload']
                )
            
            else:
                print(f"[TransportManager] Unknown lane: {lane}")
                return None
        
        except KeyError as e:
            print(f"[TransportManager] Missing field in envelope: {e}")
            return None
        except Exception as e:
            print(f"[TransportManager] Error converting envelope: {e}")
            return None
    
    async def publishCommand(self, commandEvent: dict):
        """
        Publish command to transport for producer execution.
        
        Uses canonical subject format: nova.{scopeId}.{lane}.{systemId}.{containerId}.{uniqueId}.v{version}
        Command lane uses requestId as uniqueId for routing (per architecture)
        
        Args:
            commandEvent: Command envelope with lane='command'
        """
        try:
            from .subjects import formatNovaSubject, RouteKey
            from .contract import Lane
            
            # Build RouteKey using canonical format
            # Command lane: systemId=nova (NOVA is dispatcher), uniqueId=commandId
            routeKey = RouteKey(
                scopeId=commandEvent['scopeId'],
                lane=Lane.COMMAND,
                systemId=commandEvent['systemId'],  # "nova" for CommandRequest
                containerId=commandEvent['containerId'],
                uniqueId=commandEvent['commandId'],  # commandId for routing
                schemaVersion=commandEvent.get('schemaVersion', 1)
            )
            
            subject = formatNovaSubject(routeKey)
            
            # Serialize to JSON bytes for NATS transport
            import json
            commandBytes = json.dumps(commandEvent).encode('utf-8')
            
            # Publish command envelope to transport
            await self.transport.publish(subject, commandBytes)
            
            self.log.info(f"[TransportManager] Published command: {subject}, commandId={commandEvent['commandId']}")
        
        except Exception as e:
            self.log.error(f"[TransportManager] Failed to publish command: {e}", exc_info=True)
            raise TransportError(f"Command publish failed: {e}")
