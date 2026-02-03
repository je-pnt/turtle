"""
Command lifecycle implementation for NOVA Core.

Architecture contract:
- Record-before-dispatch: CommandRequest stored in DB before dispatch
- Idempotency: requestId uniqueness enforced via DB UNIQUE constraint
- Replay blocking: timelineMode=REPLAY rejected (defense in depth)
- Atomicity: validate → record → dispatch (sequential)

Property of Uncompromising Sensors LLC.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from nova.core.contracts import TimelineMode
from nova.core.events import Lane, computeEventId, buildEntityIdentityKey
from nova.core.canonical_json import canonicalJson
from sdk.logging import getLogger


class CommandManager:
    """
    Command lifecycle manager.
    
    Handles validation, recording, dispatch, and tracking of commands.
    
    Identity model (nova architecture.md Section 3):
      - systemId: Always "nova" (NOVA issues commands)
      - containerId: The NOVA instance identifier
      - uniqueId: The requestId (unique per command request)
    """
    
    def __init__(self, database, transportManager, config: dict = None):
        self.database = database
        self.transport = transportManager
        self.config = config or {}
        self.log = getLogger()
        # NOVA instance identifier for command identity
        self.containerId = self.config.get('nodeId', 'nova-default')
    
    async def submitCommand(self, commandRequest: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit command with full lifecycle.
        
        Steps:
        1. Validate timelineMode (LIVE only)
        2. Check idempotency (requestId uniqueness)
        3. Record CommandRequest to DB
        4. Dispatch to producer via transport
        5. Return ACK
        
        Returns: ACK response dict or error dict
        """
        requestId = commandRequest['requestId']
        commandId = commandRequest['commandId']
        targetId = commandRequest['targetId']
        commandType = commandRequest['commandType']
        timelineMode = commandRequest['timelineMode']
        scopeId = commandRequest.get('scopeId', self.config.get('scopeId', 'default'))
        
        # New identity model: systemId + containerId + uniqueId
        systemId = "nova"  # Commands originate from NOVA
        containerId = self.containerId
        uniqueId = commandId  # Command lane primary identity per nova architecture.md
        
        try:
            # Step 1: Validate timelineMode (REPLAY blocked)
            if timelineMode == TimelineMode.REPLAY or timelineMode == 'replay':
                self.log.warning(f"[CommandManager] Command blocked in REPLAY mode: {commandType}")
                return {
                    'type': 'error',
                    'requestId': requestId,
                    'error': 'Commands not allowed in REPLAY mode'
                }
            
            # Step 2: Check idempotency - has this requestId been processed?
            existing = await asyncio.to_thread(
                self.database.queryCommands,
                requestId=requestId,
                limit=1
            )
            
            if existing and len(existing) > 0:
                self.log.info(f"[CommandManager] Duplicate requestId {requestId}, returning idempotent ACK")
                return {
                    'type': 'ack',
                    'requestId': requestId,
                    'message': 'Command already processed (idempotent)',
                    'commandId': commandId
                }
            
            # Step 3: Record CommandRequest to DB (record-before-dispatch)
            sourceTruthTime = datetime.now(timezone.utc).isoformat()
            
            # Build command payload for eventId computation
            commandPayload = {
                'messageType': 'CommandRequest',
                'commandId': commandId,
                'requestId': requestId,
                'targetId': targetId,
                'commandType': commandType,
                'timelineMode': str(timelineMode),
                'payload': commandRequest.get('payload', {})
            }
            
            # Compute content-derived eventId (architecture contract)
            # entityIdentityKey = systemId|containerId|uniqueId
            entityIdentityKey = buildEntityIdentityKey(systemId, containerId, uniqueId)
            canonicalPayload = canonicalJson(commandPayload)
            eventId = computeEventId(
                scopeId=scopeId,
                lane=Lane.COMMAND,
                entityIdentityKey=entityIdentityKey,
                sourceTruthTime=sourceTruthTime,
                canonicalPayload=canonicalPayload
            )
            
            commandEvent = {
                'schemaVersion': 1,  # Required envelope field per Phase 2
                'eventId': eventId,
                'scopeId': scopeId,
                'lane': 'command',
                'sourceTruthTime': sourceTruthTime,
                'systemId': systemId,
                'containerId': containerId,
                'uniqueId': uniqueId,
                'messageType': 'CommandRequest',
                'commandId': commandId,
                'requestId': requestId,
                'targetId': targetId,
                'commandType': commandType,
                'timelineMode': str(timelineMode),
                'payload': commandRequest.get('payload', {})
            }
            
            await asyncio.to_thread(
                self.database.insertCommandEvent,
                commandEvent
            )
            
            self.log.info(f"[CommandManager] Recorded CommandRequest: {commandId}, type={commandType}")
            
            # Step 4: Dispatch to producer via transport
            try:
                await self.transport.publishCommand(commandEvent)
                self.log.info(f"[CommandManager] Dispatched command {commandId} to transport")
            except Exception as e:
                # Record dispatch failure as CommandResult
                self.log.error(f"[CommandManager] Dispatch failed: {e}")
                
                # Compute eventId for failure event
                failurePayload = {
                    'messageType': 'CommandResult',
                    'commandId': commandId,
                    'status': 'failure',
                    'errorMessage': f"Dispatch failed: {str(e)}"
                }
                failureTruthTime = datetime.now(timezone.utc).isoformat()
                failureEventId = computeEventId(
                    scopeId=scopeId,
                    lane=Lane.COMMAND,
                    entityIdentityKey=buildEntityIdentityKey(systemId, containerId, f"{uniqueId}_result"),
                    sourceTruthTime=failureTruthTime,
                    canonicalPayload=canonicalJson(failurePayload)
                )
                
                failureEvent = {
                    'schemaVersion': 1,
                    'eventId': failureEventId,
                    'scopeId': scopeId,
                    'lane': 'command',
                    'sourceTruthTime': failureTruthTime,
                    'systemId': systemId,
                    'containerId': containerId,
                    'uniqueId': f"{uniqueId}_result",
                    'messageType': 'CommandResult',
                    'commandId': commandId,
                    'targetId': targetId,
                    'commandType': commandType,
                    'timelineMode': str(timelineMode),
                    'payload': {
                        'status': 'failure',
                        'errorMessage': f"Dispatch failed: {str(e)}"
                    }
                }
                await asyncio.to_thread(
                    self.database.insertCommandEvent,
                    failureEvent
                )
            
            # Step 5: Return ACK
            return {
                'type': 'ack',
                'requestId': requestId,
                'message': 'Command recorded and dispatched',
                'commandId': commandId
            }
        
        except Exception as e:
            self.log.error(f"[CommandManager] Error processing command: {e}", exc_info=True)
            return {
                'type': 'error',
                'requestId': requestId,
                'error': str(e)
            }
