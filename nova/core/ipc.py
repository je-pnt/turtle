"""
Core-side IPC handler for Server ↔ Core communication.

Core is the authoritative truth manager:
- Receives requests from Server via IPC queues
- Executes queries, streams, commands
- Sends responses back to Server

Architecture invariants:
- Core owns all DB reads/writes
- Validates all requests (timelineMode, filters, auth)
- Blocks commands in REPLAY mode
- No persistent per-client state

Property of Uncompromising Sensors LLC.
"""

import asyncio
import time
from datetime import datetime, timezone
from multiprocessing import Queue
from typing import Dict, Any, Optional

from nova.core.database import Database
from nova.core.streaming import StreamingManager
from nova.core.commands import CommandManager
from nova.core.contract import Lane
from nova.core.events import Timebase
from nova.core.contracts import (
    RequestType, TimelineMode,
    QueryRequest, StreamRequest, CancelStreamRequest, CommandRequest,
    QueryResponse, StreamChunk, StreamComplete, ErrorResponse, AckResponse,
    ExportRequest, ExportResponse, ListExportsRequest, ExportsListResponse
)
from nova.core.export import Export
from sdk.logging import getLogger


class CoreIPCHandler:
    """
    Core-side IPC handler.
    
    Receives requests from Server, executes them, sends responses.
    Runs in Core process.
    """
    
    def __init__(self, database: Database, requestQueue: Queue, responseQueue: Queue, config: dict = None):
        self.database = database
        self.requestQueue = requestQueue
        self.responseQueue = responseQueue
        self.config = config or {}
        self.log = getLogger()
        
        self.streamingManager = StreamingManager(database, config)
        
        # CommandManager needs transportManager - will be set after init
        self.commandManager: Optional[CommandManager] = None
        
        # Export handler
        from pathlib import Path
        exportDir = Path(self.config.get('exportDir', './nova/exports'))
        self.exportHandler = Export(database, exportDir)
        
        # Per-connection response queues for streaming
        self.streamQueues: Dict[str, asyncio.Queue] = {}
        
        self.running = False
    
    def setTransportManager(self, transportManager):
        """Set transport manager after Core initialization"""
        self.commandManager = CommandManager(self.database, transportManager, self.config)
        self.log.info("[CoreIPC] CommandManager initialized")
    
    async def start(self):
        """Start IPC handler loop"""
        self.running = True
        self.log.info("[CoreIPC] Started")
        
        # Start request processor
        requestTask = asyncio.create_task(self._processRequests())
        
        # Start stream response forwarder
        streamTask = asyncio.create_task(self._forwardStreamResponses())
        
        await asyncio.gather(requestTask, streamTask)
    
    async def stop(self):
        """Stop IPC handler"""
        self.running = False
        await self.streamingManager.shutdown()
        self.log.info("[CoreIPC] Stopped")
    
    async def _processRequests(self):
        """Process incoming requests from Server"""
        while self.running:
            try:
                # Check queue (non-blocking with timeout)
                request = await asyncio.to_thread(self._getRequest, timeout=0.1)
                if not request:
                    continue
                
                # Parse request
                requestType = request.get('type')
                
                if requestType == RequestType.QUERY.value:
                    await self._handleQuery(request)
                elif requestType == RequestType.START_STREAM.value:
                    await self._handleStartStream(request)
                elif requestType == RequestType.CANCEL_STREAM.value:
                    await self._handleCancelStream(request)
                elif requestType == 'setPlaybackRate':
                    await self._handleSetPlaybackRate(request)
                elif requestType == RequestType.SUBMIT_COMMAND.value:
                    await self._handleCommand(request)
                elif requestType == RequestType.EXPORT.value:
                    await self._handleExport(request)
                elif requestType == RequestType.LIST_EXPORTS.value:
                    await self._handleListExports(request)
                elif requestType == RequestType.STREAM_RAW.value:
                    await self._handleStreamRaw(request)
                elif requestType == RequestType.CANCEL_STREAM_RAW.value:
                    await self._handleCancelStreamRaw(request)
                elif requestType == RequestType.INGEST_METADATA.value:
                    await self._handleIngestMetadata(request)
                else:
                    self.log.warning(f"[CoreIPC] Unknown request type: {requestType}")
                    await self._sendError(request.get('requestId'), f"Unknown request type: {requestType}")
            
            except Exception as e:
                self.log.error(f"[CoreIPC] Error processing request: {e}")
    
    def _getRequest(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Get request from queue with timeout"""
        try:
            return self.requestQueue.get(timeout=timeout)
        except:
            return None
    
    async def _handleQuery(self, request: Dict[str, Any]):
        """Handle QueryRequest"""
        try:
            req = QueryRequest(
                requestId=request['requestId'],
                clientConnId=request['clientConnId'],
                startTime=request['startTime'],
                stopTime=request['stopTime'],
                timelineMode=TimelineMode(request['timelineMode']),
                timebase=request.get('timebase', 'canonical'),
                filters=request.get('filters')
            )
            
            self.log.info(f"[CoreIPC] Query: {req.startTime} to {req.stopTime}, "
                         f"timebase={req.timebase}, mode={req.timelineMode.value}")
            
            # Convert microseconds to ISO8601
            startTimeIso = datetime.fromtimestamp(req.startTime / 1_000_000, tz=timezone.utc).isoformat()
            stopTimeIso = datetime.fromtimestamp(req.stopTime / 1_000_000, tz=timezone.utc).isoformat()
            
            # Unpack filters
            filters = req.filters or {}
            
            # Convert string lanes to Lane enums (JS sends ['metadata'], database expects [Lane.METADATA])
            lanes = None
            if filters.get('lanes'):
                lanes = [Lane(l) if isinstance(l, str) else l for l in filters['lanes']]
                self.log.info(f"[CoreIPC] Query lanes filter: {lanes}")
            
            # Execute query with new identity model filters
            events = await asyncio.to_thread(
                self.database.queryEvents,
                startTime=startTimeIso,
                stopTime=stopTimeIso,
                timebase=req.timebase,
                scopeIds=filters.get('scopeIds'),
                lanes=lanes,
                systemId=filters.get('systemId'),
                containerId=filters.get('containerId'),
                uniqueId=filters.get('uniqueId'),
                viewId=filters.get('viewId'),
                messageType=filters.get('messageType'),
                manifestId=filters.get('manifestId'),
                requestId=filters.get('requestId'),
                limit=filters.get('limit')
            )
            
            # Send response
            # Debug: log sample event identity
            if events:
                sample = events[0]
                self.log.info(f"[CoreIPC] Query returned {len(events)} events. Sample: lane={sample.get('lane')}, systemId={sample.get('systemId')}, uniqueId={sample.get('uniqueId')}")
            
            response = QueryResponse(
                requestId=req.requestId,
                events=events,
                totalCount=len(events)
            )
            
            await self._sendResponse(response.toDict())
            
        except Exception as e:
            self.log.error(f"[CoreIPC] Query error: {e}")
            await self._sendError(request.get('requestId'), str(e))
    
    async def _handleStartStream(self, request: Dict[str, Any]):
        """Handle StreamRequest"""
        try:
            req = StreamRequest(
                requestId=request['requestId'],
                clientConnId=request['clientConnId'],
                playbackRequestId=request['playbackRequestId'],
                startTime=request['startTime'],
                stopTime=request.get('stopTime'),
                rate=request['rate'],
                timelineMode=TimelineMode(request['timelineMode']),
                timebase=request.get('timebase', 'canonical'),
                filters=request.get('filters')
            )
            
            self.log.info(f"[CoreIPC] StartStream: playbackId={req.playbackRequestId}, "
                         f"start={req.startTime}, stop={req.stopTime}, rate={req.rate}")
            
            # Create response queue for this connection
            chunkQueue = asyncio.Queue()
            self.streamQueues[req.clientConnId] = chunkQueue
            
            # Start streaming
            await self.streamingManager.startStream(req, chunkQueue)
            
            # Send ACK
            ack = AckResponse(requestId=req.requestId, message="Stream started")
            await self._sendResponse(ack.toDict())
            
        except Exception as e:
            self.log.error(f"[CoreIPC] StartStream error: {e}", exc_info=True)
            await self._sendError(request.get('requestId'), str(e))
    
    async def _handleCancelStream(self, request: Dict[str, Any]):
        """Handle CancelStreamRequest"""
        try:
            req = CancelStreamRequest(
                requestId=request['requestId'],
                clientConnId=request['clientConnId']
            )
            
            self.log.info(f"[CoreIPC] CancelStream: conn={req.clientConnId}")
            
            # Cancel stream
            await self.streamingManager.cancelStream(req.clientConnId)
            
            # Remove queue
            self.streamQueues.pop(req.clientConnId, None)
            
            # Send ACK
            ack = AckResponse(requestId=req.requestId, message="Stream canceled")
            await self._sendResponse(ack.toDict())
            
        except Exception as e:
            self.log.error(f"[CoreIPC] CancelStream error: {e}", exc_info=True)
            await self._sendError(request.get('requestId'), str(e))
    
    async def _handleSetPlaybackRate(self, request: Dict[str, Any]):
        """Handle setPlaybackRate request - change rate without restarting"""
        try:
            clientConnId = request['clientConnId']
            rate = request['rate']
            
            self.log.info(f"[CoreIPC] SetPlaybackRate: conn={clientConnId}, rate={rate}")
            
            # Update cursor rate if stream exists
            cursor = self.streamingManager.activeStreams.get(clientConnId)
            if cursor:
                cursor.rate = rate
                self.log.info(f"[CoreIPC] Updated cursor rate: conn={clientConnId}, rate={rate}")
            else:
                self.log.warning(f"[CoreIPC] No active cursor for conn={clientConnId}")
            
        except Exception as e:
            self.log.error(f"[CoreIPC] SetPlaybackRate error: {e}", exc_info=True)
    
    async def _handleCommand(self, request: Dict[str, Any]):
        """Handle CommandRequest with full lifecycle"""
        try:
            req = CommandRequest(
                requestId=request['requestId'],
                clientConnId=request['clientConnId'],
                commandId=request['commandId'],
                targetId=request['targetId'],
                commandType=request['commandType'],
                payload=request['payload'],
                timelineMode=TimelineMode(request['timelineMode']),
                userId=request.get('userId')
            )
            
            # Block commands in REPLAY mode (defense in depth)
            if req.timelineMode == TimelineMode.REPLAY:
                self.log.warning(f"[CoreIPC] Command blocked in REPLAY mode: {req.commandType}")
                await self._sendError(req.requestId, "Commands not allowed in REPLAY mode")
                return
            
            self.log.info(f"[CoreIPC] Command: type={req.commandType}, target={req.targetId}")
            
            # Use CommandManager for full lifecycle
            if not self.commandManager:
                self.log.error("[CoreIPC] CommandManager not initialized")
                await self._sendError(req.requestId, "Command infrastructure not ready")
                return
            
            result = await self.commandManager.submitCommand(req.toDict())
            await self._sendResponse(result)
            
        except Exception as e:
            self.log.error(f"[CoreIPC] Command error: {e}", exc_info=True)
            await self._sendError(request.get('requestId'), str(e))
    
    async def _forwardStreamResponses(self):
        """Forward stream chunks from queues to IPC response queue"""
        while self.running:
            try:
                # Check all active stream queues
                for clientConnId in list(self.streamQueues.keys()):
                    queue = self.streamQueues.get(clientConnId)
                    if not queue:
                        continue
                    
                    try:
                        # Non-blocking get
                        item = queue.get_nowait()
                        
                        # Send to Server
                        itemDict = item.toDict()
                        itemDict['clientConnId'] = clientConnId
                        
                        # Set type based on class
                        if item.__class__.__name__ == 'StreamComplete':
                            itemDict['type'] = 'streamComplete'
                        else:
                            itemDict['type'] = 'streamChunk'
                        
                        await self._sendResponse(itemDict)
                        
                    except asyncio.QueueEmpty:
                        pass
                
                # Small delay to prevent busy-wait
                await asyncio.sleep(0.01)
                
            except Exception as e:
                self.log.error(f"[CoreIPC] Error forwarding stream: {e}", exc_info=True)
    
    async def _sendResponse(self, response: Dict[str, Any]):
        """Send response to Server"""
        await asyncio.to_thread(self.responseQueue.put, response)
    
    async def _sendError(self, requestId: str, error: str):
        """Send error response"""
        errorResp = ErrorResponse(requestId=requestId, error=error)
        await self._sendResponse(errorResp.toDict())

    async def _handleExport(self, request: Dict[str, Any]):
        """Handle ExportRequest"""
        try:
            req = ExportRequest(
                requestId=request['requestId'],
                clientConnId=request['clientConnId'],
                startTime=request['startTime'],
                stopTime=request['stopTime'],
                timebase=request.get('timebase', 'canonical'),
                filters=request.get('filters')
            )
            
            self.log.info(f"[CoreIPC] Export: {req.startTime} to {req.stopTime}")
            
            # Convert microseconds to ISO8601
            startTimeIso = datetime.fromtimestamp(req.startTime / 1_000_000, tz=timezone.utc).isoformat()
            stopTimeIso = datetime.fromtimestamp(req.stopTime / 1_000_000, tz=timezone.utc).isoformat()
            
            # Unpack filters
            filters = req.filters or {}
            
            # Execute export (same driver codepath as fileWriter)
            result = await self.exportHandler.export(
                startTime=startTimeIso,
                stopTime=stopTimeIso,
                timebase=req.timebase,
                scopeIds=filters.get('scopeIds'),
                lanes=filters.get('lanes'),
                systemId=filters.get('systemId'),
                containerId=filters.get('containerId'),
                uniqueId=filters.get('uniqueId')
            )
            
            # Build download URL
            downloadUrl = f"/exports/{result['exportId']}.zip"
            
            response = ExportResponse(
                requestId=req.requestId,
                exportId=result['exportId'],
                downloadUrl=downloadUrl,
                eventCount=result['eventCount'],
                filesWritten=result['filesWritten']
            )
            
            await self._sendResponse(response.toDict())
            
        except Exception as e:
            self.log.error(f"[CoreIPC] Export error: {e}")
            await self._sendError(request.get('requestId'), str(e))
    
    async def _handleListExports(self, request: Dict[str, Any]):
        """Handle ListExportsRequest"""
        try:
            exports = self.exportHandler.listExports()
            
            response = ExportsListResponse(
                requestId=request['requestId'],
                exports=exports
            )
            
            await self._sendResponse(response.toDict())
            
        except Exception as e:
            self.log.error(f"[CoreIPC] ListExports error: {e}")
            await self._sendError(request.get('requestId'), str(e))

    async def _handleStreamRaw(self, request: Dict[str, Any]):
        """
        Handle STREAM_RAW for output streams (TCP/UDP/WS).
        
        Uses the SAME streaming algorithm as UI timeline (OutputStreamCursor).
        If boundInstanceId is set, follows that leader's currentTime.
        Otherwise, LIVE-follow mode.
        """
        try:
            requestId = request['requestId']
            clientConnId = request['clientConnId']
            filters = request.get('filters', {})
            boundInstanceId = request.get('boundInstanceId')
            
            self.log.info(f"[CoreIPC] StreamRaw: conn={clientConnId}, filters={filters}, bound={boundInstanceId}")
            
            # Create response queue for this stream
            chunkQueue = asyncio.Queue()
            self.streamQueues[clientConnId] = chunkQueue
            
            # Use StreamingManager's output cursor (same algorithm as UI timeline)
            await self.streamingManager.startOutputStream(
                connId=clientConnId,
                filters=filters,
                chunkQueue=chunkQueue,
                boundInstanceId=boundInstanceId
            )
            
            # Send ACK immediately
            ack = AckResponse(requestId=requestId, message="Raw stream started")
            await self._sendResponse(ack.toDict())
            
        except Exception as e:
            self.log.error(f"[CoreIPC] StreamRaw error: {e}", exc_info=True)
            await self._sendError(request.get('requestId'), str(e))
    
    async def _handleCancelStreamRaw(self, request: Dict[str, Any]):
        """
        Handle CANCEL_STREAM_RAW to stop an output stream.
        
        Uses StreamingManager to cancel the output cursor.
        """
        clientConnId = request.get('clientConnId')
        if clientConnId:
            # Cancel via StreamingManager
            await self.streamingManager.cancelOutputStream(clientConnId)
            # Also clean up local queue reference
            self.streamQueues.pop(clientConnId, None)
            self.log.info(f"[CoreIPC] CancelStreamRaw: conn={clientConnId}")
        else:
            self.log.debug(f"[CoreIPC] CancelStreamRaw: no clientConnId provided")

    async def _handleIngestMetadata(self, request: Dict[str, Any]):
        """
        Handle INGEST_METADATA to store metadata events from Server (Phase 9: chat).
        
        Server constructs the metadata envelope, Core validates and ingests.
        Returns the computed eventId for broadcast confirmation.
        """
        from .events import MetadataEvent
        from .ingest import Ingest
        
        try:
            requestId = request['requestId']
            
            # Create MetadataEvent from request
            event = MetadataEvent.create(
                scopeId=request['scopeId'],
                sourceTruthTime=request['sourceTruthTime'],
                messageType=request['messageType'],
                effectiveTime=request['effectiveTime'],
                payload=request['payload'],
                systemId=request['systemId'],
                containerId=request['containerId'],
                uniqueId=request['uniqueId']
            )
            
            # Ingest to database
            from datetime import datetime, timezone
            canonicalTruthTime = datetime.now(timezone.utc).isoformat()
            success = self.database.insertEvent(event, canonicalTruthTime)
            
            if success:
                self.log.info(f"[CoreIPC] IngestMetadata: {event.messageType} stored, eventId={event.eventId[:16]}...")
                
                # Return success with eventId
                response = {
                    'type': 'ack',
                    'requestId': requestId,
                    'eventId': event.eventId,
                    'message': 'Metadata ingested'
                }
            else:
                self.log.warning(f"[CoreIPC] IngestMetadata: duplicate eventId={event.eventId[:16]}...")
                response = {
                    'type': 'ack',
                    'requestId': requestId,
                    'eventId': event.eventId,
                    'message': 'Duplicate (already exists)'
                }
            
            await self._sendResponse(response)
            
        except Exception as e:
            self.log.error(f"[CoreIPC] IngestMetadata error: {e}", exc_info=True)
            await self._sendError(request.get('requestId'), str(e))
