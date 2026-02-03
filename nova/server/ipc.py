"""
Server-side IPC client for Server ↔ Core communication.

Server is the thin edge:
- Receives WebSocket messages from clients
- Validates auth and basic request structure
- Forwards requests to Core via IPC
- Receives responses from Core
- Routes responses back to correct client connection

Architecture invariants:
- Server is stateless (no persistent session storage)
- Core is authoritative (all validation, all DB access)
- IPC uses multiprocessing.Queue (intra-service only)

Property of Uncompromising Sensors LLC.
"""

import asyncio
import time
import uuid
from multiprocessing import Queue
from typing import Dict, Any, Optional, Callable

from nova.core.contracts import (
    RequestType, TimelineMode,
    QueryRequest, StreamRequest, CancelStreamRequest, CommandRequest,
    IngestMetadataRequest
)
from sdk.logging import getLogger


class ServerIPCClient:
    """
    Server-side IPC client.
    
    Sends requests to Core, receives responses, routes to clients.
    Runs in Server process.
    """
    
    def __init__(self, requestQueue: Queue, responseQueue: Queue):
        self.requestQueue = requestQueue
        self.responseQueue = responseQueue
        self.log = getLogger()
        
        # Response handlers: requestId → callback(response)
        self.responseHandlers: Dict[str, Callable] = {}
        
        # Stream handlers: clientConnId → callback(chunk)
        self.streamHandlers: Dict[str, Callable] = {}
        
        self.running = False
    
    async def start(self):
        """Start IPC client loop"""
        self.running = True
        self.log.info("[ServerIPC] Started")
        
        # Start response processor
        asyncio.create_task(self._processResponses())
    
    async def stop(self):
        """Stop IPC client"""
        self.running = False
        self.log.info("[ServerIPC] Stopped")
    
    async def query(self, 
                   clientConnId: str,
                   startTime: int,
                   stopTime: int,
                   timelineMode: TimelineMode,
                   timebase: str = "canonical",
                   filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send QueryRequest to Core, wait for response.
        
        Returns: QueryResponse dict
        """
        requestId = str(uuid.uuid4())
        
        request = QueryRequest(
            requestId=requestId,
            clientConnId=clientConnId,
            startTime=startTime,
            stopTime=stopTime,
            timelineMode=timelineMode,
            timebase=timebase,
            filters=filters
        )
        
        # Create response future
        future = asyncio.Future()
        self.responseHandlers[requestId] = lambda resp: future.set_result(resp)
        
        # Send request
        requestDict = request.toDict()
        requestDict['type'] = RequestType.QUERY.value
        await self._sendRequest(requestDict)
        
        # Wait for response (with timeout)
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
            return response
        finally:
            self.responseHandlers.pop(requestId, None)
    
    async def startStream(self,
                         clientConnId: str,
                         playbackRequestId: str,
                         startTime: int,
                         stopTime: Optional[int],
                         rate: float,
                         timelineMode: TimelineMode,
                         timebase: str = "canonical",
                         filters: Optional[Dict[str, Any]] = None,
                         chunkHandler: Optional[Callable] = None):
        """
        Send StreamRequest to Core.
        
        chunkHandler: callback(chunk) for each StreamChunk received
        """
        requestId = str(uuid.uuid4())
        
        request = StreamRequest(
            requestId=requestId,
            clientConnId=clientConnId,
            playbackRequestId=playbackRequestId,
            startTime=startTime,
            stopTime=stopTime,
            rate=rate,
            timelineMode=timelineMode,
            timebase=timebase,
            filters=filters
        )
        
        # Register stream handler
        if chunkHandler:
            self.streamHandlers[clientConnId] = chunkHandler
        
        # Create response future for ACK
        future = asyncio.Future()
        self.responseHandlers[requestId] = lambda resp: future.set_result(resp)
        
        # Send request
        requestDict = request.toDict()
        requestDict['type'] = RequestType.START_STREAM.value
        await self._sendRequest(requestDict)
        
        # Wait for ACK (with timeout)
        try:
            ack = await asyncio.wait_for(future, timeout=5.0)
            self.log.info(f"[ServerIPC] Stream started: playbackId={playbackRequestId}")
        finally:
            self.responseHandlers.pop(requestId, None)
    
    async def cancelStream(self, clientConnId: str):
        """Send CancelStreamRequest to Core"""
        requestId = str(uuid.uuid4())
        
        request = CancelStreamRequest(
            requestId=requestId,
            clientConnId=clientConnId
        )
        
        # Remove stream handler
        self.streamHandlers.pop(clientConnId, None)
        
        # Send request (fire and forget - don't wait for ACK)
        requestDict = request.toDict()
        requestDict['type'] = RequestType.CANCEL_STREAM.value
        await self._sendRequest(requestDict)
        
        self.log.info(f"[ServerIPC] Stream cancel sent: conn={clientConnId}")
    
    async def setPlaybackRate(self, clientConnId: str, rate: float):
        """Send SetPlaybackRate request to Core"""
        requestId = str(uuid.uuid4())
        
        request = {
            'type': 'setPlaybackRate',
            'requestId': requestId,
            'clientConnId': clientConnId,
            'rate': rate
        }
        
        await self._sendRequest(request)
        self.log.info(f"[ServerIPC] Playback rate set: conn={clientConnId}, rate={rate}")
    
    async def submitCommand(self,
                           clientConnId: str,
                           commandId: str,
                           targetId: str,
                           commandType: str,
                           payload: Dict[str, Any],
                           timelineMode: TimelineMode,
                           userId: Optional[str] = None) -> Dict[str, Any]:
        """
        Send CommandRequest to Core, wait for response.
        
        Returns: ACK or ErrorResponse dict
        """
        requestId = str(uuid.uuid4())
        
        request = CommandRequest(
            requestId=requestId,
            clientConnId=clientConnId,
            commandId=commandId,
            targetId=targetId,
            commandType=commandType,
            payload=payload,
            timelineMode=timelineMode,
            userId=userId
        )
        
        # Create response future
        future = asyncio.Future()
        self.responseHandlers[requestId] = lambda resp: future.set_result(resp)
        
        # Send request
        requestDict = request.toDict()
        requestDict['type'] = RequestType.SUBMIT_COMMAND.value
        await self._sendRequest(requestDict)
        
        # Wait for response (with timeout)
        try:
            response = await asyncio.wait_for(future, timeout=10.0)
            return response
        finally:
            self.responseHandlers.pop(requestId, None)
    
    async def ingestMetadata(self,
                             clientConnId: str,
                             scopeId: str,
                             messageType: str,
                             effectiveTime: str,
                             sourceTruthTime: str,
                             systemId: str,
                             containerId: str,
                             uniqueId: str,
                             payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send IngestMetadataRequest to Core (Phase 9: chat messages).
        
        Returns: ACK dict with eventId
        """
        requestId = str(uuid.uuid4())
        
        request = IngestMetadataRequest(
            requestId=requestId,
            clientConnId=clientConnId,
            scopeId=scopeId,
            messageType=messageType,
            effectiveTime=effectiveTime,
            sourceTruthTime=sourceTruthTime,
            systemId=systemId,
            containerId=containerId,
            uniqueId=uniqueId,
            payload=payload
        )
        
        # Create response future
        future = asyncio.Future()
        self.responseHandlers[requestId] = lambda resp: future.set_result(resp)
        
        # Send request
        requestDict = request.toDict()
        requestDict['type'] = RequestType.INGEST_METADATA.value
        await self._sendRequest(requestDict)
        
        # Wait for response (with timeout)
        try:
            response = await asyncio.wait_for(future, timeout=5.0)
            return response
        finally:
            self.responseHandlers.pop(requestId, None)
    
    async def _processResponses(self):
        """Process incoming responses from Core"""
        while self.running:
            try:
                # Check queue (non-blocking with timeout)
                response = await asyncio.to_thread(self._getResponse, timeout=0.1)
                if not response:
                    continue
                
                # Route response
                responseType = response.get('type')
                
                if responseType == 'streamChunk':
                    # Route to stream handler
                    clientConnId = response.get('clientConnId')
                    handler = self.streamHandlers.get(clientConnId)
                    if handler:
                        await handler(response)
                else:
                    # Route to response handler
                    requestId = response.get('requestId')
                    handler = self.responseHandlers.get(requestId)
                    if handler:
                        handler(response)
            
            except Exception as e:
                self.log.error(f"[ServerIPC] Error processing response: {e}", exc_info=True)
    
    def _getResponse(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Get response from queue with timeout"""
        try:
            return self.responseQueue.get(timeout=timeout)
        except:
            return None
    
    async def _sendRequest(self, request: Dict[str, Any]):
        """Send request to Core"""
        await asyncio.to_thread(self.requestQueue.put, request)
    
    async def export(self,
                    clientConnId: str,
                    startTime: int,
                    stopTime: int,
                    timebase: str = "canonical",
                    filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send ExportRequest to Core, wait for response.
        
        Returns: ExportResponse dict with exportId, downloadUrl
        """
        requestId = str(uuid.uuid4())
        
        request = {
            'requestId': requestId,
            'clientConnId': clientConnId,
            'startTime': startTime,
            'stopTime': stopTime,
            'timebase': timebase,
            'filters': filters,
            'type': RequestType.EXPORT.value
        }
        
        # Create response future
        future = asyncio.Future()
        self.responseHandlers[requestId] = lambda resp: future.set_result(resp)
        
        # Send request
        await self._sendRequest(request)
        
        # Wait for response (with longer timeout for export)
        try:
            response = await asyncio.wait_for(future, timeout=300.0)  # 5 min timeout
            return response
        finally:
            self.responseHandlers.pop(requestId, None)
    
    async def listExports(self, clientConnId: str) -> Dict[str, Any]:
        """
        Send ListExportsRequest to Core, wait for response.
        
        Returns: ExportsListResponse dict with list of exports
        """
        requestId = str(uuid.uuid4())
        
        request = {
            'requestId': requestId,
            'clientConnId': clientConnId,
            'type': RequestType.LIST_EXPORTS.value
        }
        
        # Create response future
        future = asyncio.Future()
        self.responseHandlers[requestId] = lambda resp: future.set_result(resp)
        
        # Send request
        await self._sendRequest(request)
        
        # Wait for response
        try:
            response = await asyncio.wait_for(future, timeout=10.0)
            return response
        finally:
            self.responseHandlers.pop(requestId, None)
    
    async def streamRaw(self, scopeId: str, filters: Dict[str, Any], boundInstanceId: Optional[str] = None):
        """
        Stream raw data from Core for TCP loopback (Phase 8).
        
        Yields chunks of raw events as they arrive.
        
        If boundInstanceId is set, follows that WebSocket instance's cursor.
        Otherwise, LIVE-follow.
        """
        requestId = str(uuid.uuid4())
        clientConnId = f"tcp-raw-{requestId}"
        
        # Create async queue for chunks
        chunkQueue = asyncio.Queue()
        
        # Register chunk handler (async to match _processResponses await)
        async def chunkHandler(chunk):
            chunkQueue.put_nowait(chunk)
        
        self.streamHandlers[clientConnId] = chunkHandler
        
        request = {
            'requestId': requestId,
            'clientConnId': clientConnId,
            'scopeId': scopeId,
            'filters': filters,
            'boundInstanceId': boundInstanceId,
            'type': RequestType.STREAM_RAW.value
        }
        
        # Send request
        await self._sendRequest(request)
        self.log.info(f"[ServerIPC] streamRaw started: scope={scopeId}, conn={clientConnId}, bound={boundInstanceId or 'LIVE'}")
        
        chunkCount = 0
        lastChunkTime = time.perf_counter()
        
        try:
            while True:
                waitStart = time.perf_counter()
                chunk = await chunkQueue.get()
                waitMs = (time.perf_counter() - waitStart) * 1000
                
                if chunk.get('complete'):
                    self.log.info(f"[ServerIPC] streamRaw complete: {chunkCount} chunks received")
                    break
                    
                eventCount = len(chunk.get('events', []))
                chunkCount += 1
                
                # Log occasional timing
                now = time.perf_counter()
                if chunkCount % 100 == 0 or waitMs > 200:
                    self.log.debug(f"[ServerIPC] streamRaw chunk #{chunkCount}: {eventCount} events, wait={waitMs:.1f}ms")
                lastChunkTime = now
                
                yield chunk
        except asyncio.CancelledError:
            # Stream cancelled - tell Core to stop
            self.log.info(f"[ServerIPC] streamRaw cancelled, stopping Core stream: conn={clientConnId}")
            await self._sendRequest({
                'type': RequestType.CANCEL_STREAM_RAW.value,
                'clientConnId': clientConnId
            })
            raise
        finally:
            self.streamHandlers.pop(clientConnId, None)
