"""
NOVA Publisher Adapter for hardwareService

Wraps existing hardwareService ioLayer/device plugin outputs into NOVA truth envelopes.
Publishes via sdk.transport using NOVA subject pattern.

Architecture Contract:
- Minimal changes: hooks existing data flow, no new plugin architecture
- Publishes Raw + Parsed + Command + Metadata lanes
- Computes eventId before publishing (producer responsibility)
- Uses RFC 8785 JCS for cross-language eventId stability

Identity Model (nova architecture.md Section 3):
  Public identity is always: scopeId + lane + systemId + containerId + uniqueId
  - systemId: "hardwareService" (this adapter's data system)
  - containerId: config.containerId or hostname (node/payload instance)
  - uniqueId: deviceId for Raw, streamId for Parsed, commandId for Command, etc.

  entityIdentityKey = systemId|containerId|uniqueId (for eventId hash)

  Optional debug fields (not part of identity):
  - connectionId: TCP/serial source. Optional debug label.
  - sequence: Frame sequence for Raw. Optional debug.
  - streamId: Semantic stream type. Optional technical label.

Integration:
- hardwareService initializes NovaAdapter with config and transport
- NovaAdapter wraps device emit() calls
- Device plugins remain unchanged

Property of Uncompromising Sensors LLC.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# NOVA imports (relative to SDK root)
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from nova.core.subjects import formatNovaSubject, RouteKey
from nova.core.canonical_json import canonicalJson, canonicalJsonBytes
from nova.core.contract import Lane
from sdk.logging import getLogger


def jsonNormalize(obj):
    """
    Normalize Python objects for JSON compatibility.
    
    Handles mixed-type dict keys (int/str) by converting to JSON and back.
    This ensures all keys are strings and canonicalJson can sort them.
    
    Args:
        obj: Python object (dict, list, primitives)
        
    Returns:
        JSON-normalized object (all dict keys as strings)
    """
    return json.loads(json.dumps(obj))


class NovaAdapter:
    """
    NOVA publisher adapter for hardwareService.
    
    Wraps device data emissions into NOVA truth envelopes using new identity model:
    - systemId: "hardwareService" (constant for this adapter)
    - containerId: from config (node/payload instance)
    - uniqueId: deviceId (Raw), streamId (Parsed), commandId:messageType (Command)
    """
    
    def __init__(self, config: dict, novaTransport, hardwareService=None):
        """
        Initialize NOVA adapter.
        
        Args:
            config: hardwareService config with scopeId and containerId
            novaTransport: Separate transport connection for NOVA (sdk.transport instance)
            hardwareService: Reference to HardwareService instance (for command dispatch)
        """
        self.log = getLogger()
        self.scopeId = config.get('scopeId', 'unknown')
        self.systemId = "hardwareService"  # Data system identity (constant)
        self.containerId = config.get('containerId', config.get('nodeId', 'node1'))  # Instance identity
        self.novaTransport = novaTransport
        self.schemaVersion = 1  # NOVA envelope v1
        self._running = False
        self.hardwareService = hardwareService
        self.commandSubscription = None
        
        self.log.info('[NovaAdapter] Initialized', scopeId=self.scopeId, 
                     systemId=self.systemId, containerId=self.containerId)
    
    async def start(self):
        """Start NOVA adapter (transport already connected by main)."""
        if self._running:
            return
        
        self.log.info('[NovaAdapter] Starting...')
        self._running = True
        
        # Subscribe to commands if hardwareService is configured
        # Subject contract: nova.{scopeId}.command.nova.{containerId}.{commandId}.v1
        # Use wildcard on commandId to receive all commands for this scope/container
        if self.hardwareService:
            commandSubject = f"nova.{self.scopeId}.command.nova.*.*.v1"
            self.log.info(f'[NovaAdapter] Subscribing to commands: {commandSubject}')
            self.commandSubscription = await self.novaTransport.subscribe(
                subject=commandSubject,
                handler=self._handleCommand
            )
    
    async def stop(self):
        """Stop NOVA adapter and clean up."""
        if not self._running:
            return
        
        self.log.info('[NovaAdapter] Stopping...')
        
        # Unsubscribe from commands
        if self.commandSubscription:
            await self.commandSubscription.unsubscribe()
            self.commandSubscription = None
        
        self._running = False
    
    def _buildEntityIdentityKey(self, uniqueId: str) -> str:
        """Build entity identity key for eventId hash."""
        return f"{self.systemId}|{self.containerId}|{uniqueId}"
    
    async def publishRaw(self, deviceId: str, sequence: int, rawBytes: bytes):
        """
        Publish Raw lane event.
        
        Identity model:
        - systemId: "hardwareService"
        - containerId: from config
        - uniqueId: deviceId
        - connectionId: optional debug label (conn-{deviceId})
        - sequence: optional debug
        
        Args:
            deviceId: Device identifier (becomes uniqueId)
            sequence: Frame sequence number (optional debug)
            rawBytes: Raw frame bytes
        """
        if not self._running:
            return
        
        uniqueId = deviceId
        connectionId = f"conn-{deviceId}"  # Optional debug label
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        
        # Build entity identity key for eventId
        entityIdentityKey = self._buildEntityIdentityKey(uniqueId)
        
        # Compute eventId (Raw lane hashes bytes directly)
        eventId = self._computeEventId(
            lane=Lane.RAW,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=rawBytes
        )
        
        # Build envelope with new identity model
        envelope = {
            "schemaVersion": self.schemaVersion,
            "eventId": eventId,
            "scopeId": self.scopeId,
            "lane": "raw",
            "sourceTruthTime": sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": uniqueId,
            "connectionId": connectionId,  # Optional debug
            "sequence": sequence,  # Optional debug
            "bytes": rawBytes.hex()  # Hex-encode for JSON transport
        }
        
        # Format subject using canonical format
        routeKey = RouteKey(
            scopeId=self.scopeId,
            lane=Lane.RAW,
            systemId=self.systemId,
            containerId=self.containerId,
            uniqueId=uniqueId,
            schemaVersion=self.schemaVersion
        )
        subject = formatNovaSubject(routeKey)
        
        # Publish (errors propagate - no swallowing)
        await self.novaTransport.publish(subject, json.dumps(envelope).encode('utf-8'))
        
        self.log.debug('[NovaAdapter] Published Raw', 
                      eventId=eventId[:16], uniqueId=uniqueId, sequence=sequence)
    
    async def publishParsed(self, deviceId: str, streamId: str, streamType: str, 
                           payload: dict):
        """
        Publish Parsed lane event.
        
        Identity model:
        - systemId: "hardwareService"
        - containerId: from config
        - uniqueId: deviceId (the renderable entity)
        - messageType: streamType (lane-internal message identity)
        
        Args:
            deviceId: Device identifier (becomes uniqueId - the renderable entity)
            streamId: Stream identifier (optional, for driver routing)
            streamType: Stream type (e.g., 'ubx.nav.pvt') - becomes messageType
            payload: Parsed data dictionary
        """
        if not self._running:
            return
        
        # uniqueId = device (renderable entity), NOT stream
        uniqueId = deviceId
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        
        # Normalize payload to JSON-compatible form (converts int keys to strings)
        normalizedPayload = jsonNormalize(payload)
        
        # Canonicalize payload for eventId
        canonicalPayload = canonicalJson(normalizedPayload)
        
        # Build entity identity key for eventId
        entityIdentityKey = self._buildEntityIdentityKey(uniqueId)
        
        # Compute eventId
        eventId = self._computeEventId(
            lane=Lane.PARSED,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        
        # Build envelope with new identity model
        envelope = {
            "schemaVersion": self.schemaVersion,
            "eventId": eventId,
            "scopeId": self.scopeId,
            "lane": "parsed",
            "sourceTruthTime": sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": uniqueId,
            "messageType": streamType,  # Required: message type (e.g., ubx.nav.pvt)
            "payload": normalizedPayload
        }
        
        # Format subject using canonical format
        routeKey = RouteKey(
            scopeId=self.scopeId,
            lane=Lane.PARSED,
            systemId=self.systemId,
            containerId=self.containerId,
            uniqueId=uniqueId,
            schemaVersion=self.schemaVersion
        )
        subject = formatNovaSubject(routeKey)
        
        # Publish (errors propagate - no swallowing)
        await self.novaTransport.publish(subject, json.dumps(envelope).encode('utf-8'))
        
        self.log.debug('[NovaAdapter] Published Parsed', 
                      eventId=eventId[:16], uniqueId=uniqueId, streamType=streamType)
    
    # NOTE: Position emission is handled by the device/parser itself (e.g., ubxDevice.py)
    # NOT here in NovaAdapter. This keeps NovaAdapter protocol-agnostic.
    # Each device knows when it has position data and emits 'Position' messageType directly.
    
    async def publishEntityDescriptor(self, deviceId: str, entityType: str, 
                                      displayName: str = None, description: str = None,
                                      capabilities: list = None):
        """
        Publish entity descriptor to Metadata lane.
        
        This announces the entity with its type so the UI can select the
        appropriate card manifest for display.
        
        Args:
            deviceId: Device identifier (becomes uniqueId)
            entityType: Entity type (e.g., 'gnss-receiver', 'ubx', 'spectrum-analyzer')
            displayName: Human-readable name (defaults to deviceId)
            description: Optional description
            capabilities: Optional list of capabilities
        """
        payload = {
            'entityType': entityType,
            'displayName': displayName or deviceId,
            'deviceId': deviceId,
        }
        if description:
            payload['description'] = description
        if capabilities:
            payload['capabilities'] = capabilities
        
        await self.publishMetadata(
            messageType='ProducerDescriptor',
            streamId=None,
            manifestId=None,
            payload=payload,
            entityId=deviceId
        )
        
        self.log.info(f'[NovaAdapter] Published entity descriptor: {deviceId} ({entityType})')

    async def publishMetadata(self, messageType: str, streamId: Optional[str],
                             manifestId: Optional[str], payload: dict,
                             effectiveTime: Optional[str] = None,
                             entityId: Optional[str] = None):
        """
        Publish Metadata lane event.
        
        Identity model:
        - systemId: "hardwareService"
        - containerId: from config
        - uniqueId: entityId (the renderable entity this metadata describes)
        - messageType: metadata type (e.g., 'ProducerDescriptor')
        
        Args:
            messageType: Metadata message type (e.g., 'ProducerDescriptor')
            streamId: Stream identifier (optional, for driver routing - NOT identity)
            manifestId: Manifest identifier (becomes uniqueId if entityId not provided)
            payload: Metadata payload dictionary
            effectiveTime: Optional effective time (defaults to sourceTruthTime)
            entityId: The renderable entity this metadata describes (preferred for uniqueId)
        """
        if not self._running:
            return
        
        if not entityId and not manifestId:
            self.log.error('[NovaAdapter] Metadata requires entityId or manifestId')
            return
        
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        effectiveTime = effectiveTime or sourceTruthTime
        
        # uniqueId = renderable entity (entityId preferred, fall back to manifestId)
        uniqueId = entityId if entityId else manifestId
        
        # Normalize payload to JSON-compatible form
        normalizedPayload = jsonNormalize(payload)
        
        # Canonicalize payload for eventId
        canonicalPayload = canonicalJson(normalizedPayload)
        
        # Build entity identity key for eventId
        entityIdentityKey = self._buildEntityIdentityKey(uniqueId)
        
        # Compute eventId
        eventId = self._computeEventId(
            lane=Lane.METADATA,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        
        # Build envelope with new identity model
        envelope = {
            "schemaVersion": self.schemaVersion,
            "eventId": eventId,
            "scopeId": self.scopeId,
            "lane": "metadata",
            "sourceTruthTime": sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": uniqueId,
            "messageType": messageType,
            "effectiveTime": effectiveTime,
            "payload": normalizedPayload
        }
        
        # Optional technical labels
        if streamId:
            envelope["streamId"] = streamId
        if manifestId:
            envelope["manifestId"] = manifestId
        
        # Format subject using canonical format
        routeKey = RouteKey(
            scopeId=self.scopeId,
            lane=Lane.METADATA,
            systemId=self.systemId,
            containerId=self.containerId,
            uniqueId=uniqueId,
            schemaVersion=self.schemaVersion
        )
        subject = formatNovaSubject(routeKey)
        
        # Publish (errors propagate - no swallowing)
        await self.novaTransport.publish(subject, json.dumps(envelope).encode('utf-8'))
        
        self.log.debug('[NovaAdapter] Published Metadata', 
                      eventId=eventId[:16], messageType=messageType, uniqueId=uniqueId)

    async def publishUiUpdate(self, deviceId: str, viewId: str, 
                             manifestId: str, manifestVersion: str,
                             data: dict):
        """
        Publish UiUpdate event to UI lane.
        
        Identity model (Phase 7):
        - systemId: "hardwareService"
        - containerId: from config
        - uniqueId: deviceId (the renderable entity)
        - viewId: view being updated
        - manifestId + manifestVersion: manifest reference
        
        UI updates are partial upserts - only include changed keys.
        
        Args:
            deviceId: Device identifier (becomes uniqueId - the renderable entity)
            viewId: View identifier (e.g., 'telemetry.gnss', 'ui.shield')
            manifestId: Manifest identifier
            manifestVersion: Manifest version
            data: Partial state update dictionary
        """
        if not self._running:
            return
        
        uniqueId = deviceId
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        
        # Normalize data to JSON-compatible form
        normalizedData = jsonNormalize(data)
        
        # Canonicalize data for eventId
        canonicalPayload = canonicalJson(normalizedData)
        
        # Build entity identity key for eventId
        entityIdentityKey = self._buildEntityIdentityKey(uniqueId)
        
        # Compute eventId
        eventId = self._computeEventId(
            lane=Lane.UI,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        
        # Build envelope with UI lane identity model
        envelope = {
            "schemaVersion": self.schemaVersion,
            "eventId": eventId,
            "scopeId": self.scopeId,
            "lane": "ui",
            "sourceTruthTime": sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": uniqueId,
            "messageType": "UiUpdate",
            "viewId": viewId,
            "manifestId": manifestId,
            "manifestVersion": manifestVersion,
            "data": normalizedData
        }
        
        # Format subject using canonical format
        routeKey = RouteKey(
            scopeId=self.scopeId,
            lane=Lane.UI,
            systemId=self.systemId,
            containerId=self.containerId,
            uniqueId=uniqueId,
            schemaVersion=self.schemaVersion
        )
        subject = formatNovaSubject(routeKey)
        
        # Publish (errors propagate - no swallowing)
        await self.novaTransport.publish(subject, json.dumps(envelope).encode('utf-8'))
        
        self.log.debug('[NovaAdapter] Published UiUpdate', 
                      eventId=eventId[:16], uniqueId=uniqueId, viewId=viewId)

    
    def _computeEventId(self, lane: Lane, entityIdentityKey: str, sourceTruthTime: str,
                       canonicalPayload: Any) -> str:
        """
        Compute content-derived eventId (producer responsibility).
        
        Hash: SHA256(eidV1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload)
        
        entityIdentityKey = systemId|containerId|uniqueId (universal for all lanes)
        
        Args:
            lane: Event lane
            entityIdentityKey: Universal entity identity (systemId|containerId|uniqueId)
            sourceTruthTime: ISO8601 timestamp
            canonicalPayload: Canonical JSON string or raw bytes
            
        Returns:
            Hex-encoded SHA256 hash
        """
        hasher = hashlib.sha256()
        
        # Version prefix
        hasher.update(b"eidV1")
        hasher.update(self.scopeId.encode('utf-8'))
        hasher.update(lane.value.encode('utf-8'))
        hasher.update(entityIdentityKey.encode('utf-8'))
        hasher.update(sourceTruthTime.encode('utf-8'))
        
        if isinstance(canonicalPayload, bytes):
            hasher.update(canonicalPayload)
        else:
            hasher.update(canonicalPayload.encode('utf-8'))
        
        return hasher.hexdigest()
    
    async def _handleCommand(self, subject: str, data: bytes):
        """
        Handle incoming command from NOVA Core.
        
        Producer contract (optional response):
        1. Parse CommandRequest envelope
        2. Dispatch to appropriate device plugin
        3. Optionally publish CommandProgress during execution
        4. Optionally publish CommandResult on completion
        
        Args:
            subject: NATS subject (nova.{scopeId}.command.nova.{containerId}.{commandId}.v1)
            data: CommandRequest envelope (JSON bytes)
        """
        self.log.info(f'[NovaAdapter] _handleCommand received on {subject}')
        try:
            # Parse envelope
            if isinstance(data, bytes):
                envelope = json.loads(data.decode('utf-8'))
            else:
                envelope = data
            
            self.log.info(f'[NovaAdapter] Command envelope: messageType={envelope.get("messageType")}, targetId={envelope.get("targetId")}')
            
            # Only process CommandRequest messages (ignore Progress/Result we receive from ourselves)
            messageType = envelope.get('messageType')
            if messageType != 'CommandRequest':
                # Silently ignore - this is likely our own Progress/Result echoed back via wildcard subscription
                return
            
            commandId = envelope['commandId']
            # requestId not needed by producer (NOVA-internal idempotency mechanism)
            targetId = envelope['targetId']
            commandType = envelope['commandType']
            payload = envelope.get('payload', {})
            
            self.log.info(f'[NovaAdapter] Command received: {commandType}',
                         commandId=commandId, targetId=targetId)
            
            # Find target device in hardwareService
            deviceEntry = self.hardwareService.devices.get(targetId)
            
            if not deviceEntry:
                availableDevices = list(self.hardwareService.devices.keys())
                self.log.warning(f'[NovaAdapter] Target device not found: {targetId}', availableDevices=availableDevices)
                await self._publishCommandResult(
                    commandId, targetId, commandType,
                    status='failure',
                    errorMessage=f"Device not found: {targetId}"
                )
                return
            
            # Extract actual device object from entry
            device = deviceEntry['device']
            
            # Execute command with optional progress tracking
            await self._executeCommand(device, commandId, targetId, commandType, payload)
        
        except Exception as e:
            self.log.error(f'[NovaAdapter] Command handler error: {e}', exc_info=True)
            # Try to publish failure result if we have enough info
            try:
                if 'commandId' in locals():
                    await self._publishCommandResult(
                        commandId, targetId or 'unknown', commandType or 'unknown',
                        status='failure',
                        errorMessage=f"Command handler error: {str(e)}"
                    )
            except:
                pass
    
    async def _executeCommand(self, device, commandId: str,
                             targetId: str, commandType: str, payload: dict):
        """
        Execute command on device with optional progress tracking.
        
        Producer contract: Progress and Result are optional enrichments.
        hardwareService provides them for operator visibility.
        
        Args:
            device: Device plugin instance
            commandId: Unique command ID (correlation key)
            targetId: Target device ID
            commandType: Command type name
            payload: Command parameters
        """
        try:
            # Publish initial progress
            await self._publishCommandProgress(
                commandId, targetId, commandType,
                progress=0, message="Command received"
            )
            
            # Check if device has command handler method
            commandMethod = getattr(device, f'cmd_{commandType}', None)
            
            # Debug: log device type and available methods
            if not commandMethod:
                deviceType = type(device).__name__
                availableMethods = [m for m in dir(device) if m.startswith('cmd_')]
                self.log.info(f'[NovaAdapter] Device type: {deviceType}, available cmd_ methods: {availableMethods}')
                
                # Fallback: generic handleCommand method
                commandMethod = getattr(device, 'handleCommand', None)
            
            if not commandMethod:
                self.log.warning(f'[NovaAdapter] Device {targetId} has no handler for command: {commandType}')
                await self._publishCommandResult(
                    commandId, targetId, commandType,
                    status='failure',
                    errorMessage=f"Command not supported: {commandType}"
                )
                return
            
            # Publish mid-execution progress
            await self._publishCommandProgress(
                commandId, targetId, commandType,
                progress=50, message="Executing..."
            )
            
            # Execute command (pass payload, commandType for generic handler)
            if commandMethod.__name__ == 'handleCommand':
                result = await commandMethod(commandType, payload)
            else:
                result = await commandMethod(**payload)
            
            # Publish completion progress
            await self._publishCommandProgress(
                commandId, targetId, commandType,
                progress=100, message="Complete"
            )
            
            # Publish success result
            await self._publishCommandResult(
                commandId, targetId, commandType,
                status='success',
                resultData=result
            )
            
            self.log.info(f'[NovaAdapter] Command completed: {commandType}',
                         commandId=commandId, targetId=targetId)
        
        except Exception as e:
            self.log.error(f'[NovaAdapter] Command execution error: {e}', exc_info=True)
            await self._publishCommandResult(
                commandId, targetId, commandType,
                status='failure',
                errorMessage=str(e)
            )
    
    async def _publishCommandProgress(self, commandId: str,
                                     targetId: str, commandType: str,
                                     progress: int, message: str):
        """
        Publish CommandProgress event.
        
        Identity model:
        - systemId: "hardwareService"
        - containerId: from config
        - uniqueId: "{commandId}:CommandProgress"
        
        Args:
            commandId: Command ID (correlation key)
            targetId: Target device ID
            commandType: Command type
            progress: Progress percentage (0-100)
            message: Progress message
        """
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        
        progressPayload = {
            "progress": progress,
            "message": message
        }
        
        # uniqueId for command lane: commandId:messageType
        uniqueId = f"{commandId}:CommandProgress"
        
        # Build entity identity key for eventId
        entityIdentityKey = self._buildEntityIdentityKey(uniqueId)
        
        # Compute eventId
        canonicalPayload = canonicalJson(progressPayload)
        eventId = self._computeEventId(
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        
        # Build envelope with new identity model
        envelope = {
            "schemaVersion": self.schemaVersion,
            "eventId": eventId,
            "scopeId": self.scopeId,
            "lane": "command",
            "sourceTruthTime": sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": uniqueId,
            "messageType": "CommandProgress",
            "commandId": commandId,
            "targetId": targetId,
            "commandType": commandType,
            "payload": progressPayload
        }
        
        # Format subject using canonical format
        routeKey = RouteKey(
            scopeId=self.scopeId,
            lane=Lane.COMMAND,
            systemId=self.systemId,
            containerId=self.containerId,
            uniqueId=uniqueId,
            schemaVersion=self.schemaVersion
        )
        subject = formatNovaSubject(routeKey)
        
        # Publish
        await self.novaTransport.publish(subject, json.dumps(envelope).encode('utf-8'))
        
        self.log.debug(f'[NovaAdapter] Published CommandProgress: {progress}%',
                      commandId=commandId)
    
    async def _publishCommandResult(self, commandId: str,
                                    targetId: str, commandType: str,
                                    status: str, errorMessage: str = None,
                                    resultData: Any = None):
        """
        Publish CommandResult event.
        
        Identity model:
        - systemId: "hardwareService"
        - containerId: from config
        - uniqueId: "{commandId}:CommandResult"
        
        Args:
            commandId: Command ID (correlation key)
            targetId: Target device ID
            commandType: Command type
            status: 'success' or 'failure'
            errorMessage: Error message (if failure)
            resultData: Result data (if success)
        """
        sourceTruthTime = datetime.now(timezone.utc).isoformat()
        
        resultPayload = {"status": status}
        if errorMessage:
            resultPayload["errorMessage"] = errorMessage
        if resultData:
            resultPayload["result"] = resultData
        
        # uniqueId for command lane: commandId:messageType
        uniqueId = f"{commandId}:CommandResult"
        
        # Build entity identity key for eventId
        entityIdentityKey = self._buildEntityIdentityKey(uniqueId)
        
        # Compute eventId
        canonicalPayload = canonicalJson(resultPayload)
        eventId = self._computeEventId(
            lane=Lane.COMMAND,
            entityIdentityKey=entityIdentityKey,
            sourceTruthTime=sourceTruthTime,
            canonicalPayload=canonicalPayload
        )
        
        # Build envelope with new identity model
        envelope = {
            "schemaVersion": self.schemaVersion,
            "eventId": eventId,
            "scopeId": self.scopeId,
            "lane": "command",
            "sourceTruthTime": sourceTruthTime,
            "systemId": self.systemId,
            "containerId": self.containerId,
            "uniqueId": uniqueId,
            "messageType": "CommandResult",
            "commandId": commandId,
            "targetId": targetId,
            "commandType": commandType,
            "payload": resultPayload
        }
        
        # Format subject using canonical format
        routeKey = RouteKey(
            scopeId=self.scopeId,
            lane=Lane.COMMAND,
            systemId=self.systemId,
            containerId=self.containerId,
            uniqueId=uniqueId,
            schemaVersion=self.schemaVersion
        )
        subject = formatNovaSubject(routeKey)
        
        # Publish
        await self.novaTransport.publish(subject, json.dumps(envelope).encode('utf-8'))
        
        self.log.info(f'[NovaAdapter] Published CommandResult: {status}',
                     commandId=commandId)
