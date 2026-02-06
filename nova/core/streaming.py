"""
Server-paced streaming playback from truth database.

Implements ephemeral cursor-based streaming with playback rate control.
No persistent per-client state - cursors exist only during active stream.

Architecture invariants (nova architecture.md):
- Server-paced: Core controls emission timing based on requested rate
- Ephemeral: cursor state discarded when stream stops/canceled
- Fencing: playbackRequestId prevents interleaving after seek/rate change
- Deterministic: uses ordering.py for event sequencing
- Stateless: no session storage, restart from any time T

Identity Model (nova architecture.md Section 3):
  Filters use universal identity: systemId + containerId + uniqueId

Property of Uncompromising Sensors LLC.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

from nova.core.database import Database
from nova.core.contracts import StreamRequest, StreamChunk, StreamComplete, TimelineMode
from nova.core.contract import Lane
from sdk.logging import getLogger


class StreamCursor:
    """Ephemeral cursor for one active stream"""
    
    def __init__(self, request: StreamRequest, database: Database, config: dict = None):
        self.request = request
        self.database = database
        self.log = getLogger()
        
        # Handle startTime/stopTime (may be ISO string or microsecond int)
        if request.startTime:
            if isinstance(request.startTime, str):
                # Convert ISO string to microseconds
                dt = datetime.fromisoformat(request.startTime.replace('Z', '+00:00'))
                self.startTime = int(dt.timestamp() * 1_000_000)
            else:
                # Already microseconds
                self.startTime = request.startTime
        else:
            self.startTime = None
        
        if request.stopTime:
            if isinstance(request.stopTime, str):
                # Convert ISO string to microseconds
                dt = datetime.fromisoformat(request.stopTime.replace('Z', '+00:00'))
                self.stopTime = int(dt.timestamp() * 1_000_000)
            else:
                # Already microseconds
                self.stopTime = request.stopTime
        else:
            self.stopTime = None
        
        # Initialize cursor based on direction
        # Forward: start at startTime, move toward stopTime
        # Backward: start at stopTime, move toward startTime
        if request.rate < 0 and self.stopTime:
            self.currentTime = self.stopTime  # Start at end
        else:
            self.currentTime = self.startTime  # Start at beginning
        
        self.rate = request.rate
        self.timebase = request.timebase
        self.filters = request.filters or {}
        self.playbackRequestId = request.playbackRequestId
        self.clientConnId = request.clientConnId
        
        self.running = True
        
        # For LIVE streaming (stopTime=None): notification event for push-based updates
        if self.stopTime is None:
            self.newDataEvent = asyncio.Event()
        
        # Event to notify followers when cursor advances (for bound output streams)
        self.cursorAdvancedEvent = asyncio.Event()
        self.lastWindow = None  # (t0, t1) tuple for followers to query
        
    async def streamChunks(self, chunkQueue: asyncio.Queue):
        """
        Stream events server-paced according to rate.
        
        Architecture: Infinite streaming (stopTime=null) for both LIVE and REWIND.
        - LIVE (rate > 0, stopTime=null): Notification-driven, emit new data immediately
        - REWIND (rate < 0, stopTime=null): Query historical data, pace at requested rate
        - Bounded REWIND (stopTime set): Query until boundary, pace at requested rate
        """
        self.log.info(f"[Stream] Start: requestId={self.playbackRequestId}, "
                     f"window=[{self.startTime}, {self.stopTime}], cursor={self.currentTime}, rate={self.rate}")
        
        # LIVE mode: only when no startTime provided (truly following live edge)
        isLive = (self.startTime is None)
        
        try:
            chunkCount = 0
            while self.running:
                # Check stop condition (only for bounded streams with stopTime set)
                if self.stopTime is not None:
                    if self.rate >= 0:
                        # Forward bounded: stop when cursor reaches or passes end boundary
                        if self.currentTime >= self.stopTime:
                            self.log.info(f"[Stream] Complete (reached stop): requestId={self.playbackRequestId}")
                            await chunkQueue.put(StreamComplete(playbackRequestId=self.playbackRequestId))
                            break
                    else:
                        # Backward bounded: stop when cursor reaches or passes start boundary
                        if self.currentTime <= self.startTime:
                            self.log.info(f"[Stream] Complete (reached stop): requestId={self.playbackRequestId}")
                            await chunkQueue.put(StreamComplete(playbackRequestId=self.playbackRequestId))
                            break
                
                # Read next chunk from database (includes all lanes - metadata flows naturally)
                events = await self._readNextChunk()
                
                if not events:
                    # No data in current window - emit empty chunk to advance timeline
                    if isLive:
                        # LIVE mode: wait for notification of new data
                        self.log.debug(f"[Stream] LIVE no data, cursor={self.currentTime}, waiting for notification")
                        await self.newDataEvent.wait()
                        self.newDataEvent.clear()
                        continue
                    elif self.stopTime is None:
                        # Infinite REWIND: emit cursor advance even with no data (always-move-time)
                        # This prevents getting stuck at times with no data
                        chunk = StreamChunk(
                            playbackRequestId=self.playbackRequestId,
                            events=[],
                            timestamp=self.currentTime,
                            complete=False
                        )
                        await chunkQueue.put(chunk)
                        self.cursorAdvancedEvent.set()
                        
                        # Apply paced delay for smooth timeline advancement
                        queryWindowUs = getattr(self, 'lastQueryWindowUs', 1_000_000)
                        await self._pacedDelay(queryWindowUs)
                        continue
                    else:
                        # Bounded streaming: continue scanning (data may be sparse)
                        # Only complete when cursor reaches boundary (checked at loop start)
                        continue
                
                # Emit chunk with cursor position (server-driven timeline)
                chunkCount += 1
                if isLive and (chunkCount <= 3 or chunkCount % 50 == 0):
                    self.log.info(f"[Stream] LIVE chunk #{chunkCount}: {len(events)} events, "
                                f"cursor={self.lastEmittedCursor}, window={self.lastWindow}")
                chunk = StreamChunk(
                    playbackRequestId=self.playbackRequestId,
                    events=events,
                    timestamp=self.lastEmittedCursor,
                    complete=False
                )
                await chunkQueue.put(chunk)
                
                # Signal followers that cursor has advanced (they read lastWindow)
                self.cursorAdvancedEvent.set()
                
                # Server-paced delay: ONLY for REWIND mode
                # LIVE mode has natural pacing from data arrival rate
                if not isLive:
                    queryWindowUs = getattr(self, 'lastQueryWindowUs', 1_000_000)
                    await self._pacedDelay(queryWindowUs)
                
        except asyncio.CancelledError:
            self.log.info(f"[Stream] Canceled: requestId={self.playbackRequestId}")
            raise
        except Exception as e:
            self.log.error(f"[Stream] Error: {e}", exc_info=True)
            raise
    
    async def _readNextChunk(self) -> List[Dict[str, Any]]:
        """
        Read next batch of events from database.
        Architecture: Small time windows for smooth continuous streaming.
        - LIVE initial: Read last 1 minute to catch up (includes metadata)
        - LIVE ongoing: Read from cursor to now
        - REWIND: Read 1-second windows for smooth flow at typical data rates
        """
        # Query window size: 1 second of timeline data for smooth continuous flow
        # At 10Hz: ~10 events, at 100Hz: ~100 events - reasonable batch sizes
        queryWindowUs = 1_000_000  # 1 second in microseconds
        
        # Calculate query boundaries
        # LIVE only if no startTime was provided
        isLive = (self.startTime is None)
        nowUs = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
        
        if self.currentTime is None:
            # LIVE mode: start from now (no lookback — UI state is a query concern)
            readStart = nowUs
            readEnd = nowUs
            actualWindowUs = 0
            self.currentTime = nowUs
            self.log.info(f"[Stream] LIVE start from now")
        elif self.rate >= 0:
            # Forward: read window from cursor
            readStart = self.currentTime
            if self.stopTime:
                # Bounded forward: don't exceed stop boundary
                readEnd = min(self.currentTime + queryWindowUs, self.stopTime)
            elif isLive:
                # LIVE mode: query from cursor to now
                readEnd = nowUs
                gapUs = nowUs - self.currentTime
                if gapUs < 0:
                    self.log.warning(f"[Stream] LIVE cursor AHEAD of now by {-gapUs}us — clock skew?")
                elif gapUs > 5_000_000:
                    self.log.info(f"[Stream] LIVE gap={gapUs/1_000_000:.1f}s (cursor behind now)")
            else:
                # Infinite forward (non-LIVE): read 1-second window
                readEnd = self.currentTime + queryWindowUs
            actualWindowUs = readEnd - readStart
        else:
            # Backward: read window before cursor
            readEnd = self.currentTime
            if self.stopTime:
                # Bounded backward: don't go past start boundary
                readStart = max(self.currentTime - queryWindowUs, self.startTime)
            else:
                # Infinite backward (REWIND): read 1-second window backward
                readStart = self.currentTime - queryWindowUs
            actualWindowUs = readEnd - readStart
        
        # Convert to ISO8601
        startTimeIso = datetime.fromtimestamp(readStart / 1_000_000, tz=timezone.utc).isoformat()
        stopTimeIso = datetime.fromtimestamp(readEnd / 1_000_000, tz=timezone.utc).isoformat()
        
        # Determine lanes to query (default: all lanes)
        # Convert string lanes to Lane enums (JS sends ['metadata'], database expects [Lane.METADATA])
        requestedLanes = self.filters.get('lanes')
        if requestedLanes:
            requestedLanes = [Lane(l) if isinstance(l, str) else l for l in requestedLanes]
        
        # Query database - NO LIMIT, read what exists
        # Filters use new identity model: systemId, containerId, uniqueId
        events = await asyncio.to_thread(
            self.database.queryEvents,
            startTime=startTimeIso,
            stopTime=stopTimeIso,
            timebase=self.timebase,
            scopeIds=self.filters.get('scopeIds'),
            lanes=requestedLanes,
            systemId=self.filters.get('systemId'),
            containerId=self.filters.get('containerId'),
            uniqueId=self.filters.get('uniqueId'),
            viewId=self.filters.get('viewId'),
            messageType=self.filters.get('messageType'),
            manifestId=self.filters.get('manifestId'),
            commandType=self.filters.get('commandType')
        )
        
        if not events:
            # No events in current window
            # LIVE mode: don't advance cursor (wait for notification, re-query same window)
            # REWIND: advance cursor to continue scanning historical data
            if not isLive:
                if self.rate >= 0:
                    self.currentTime = readEnd
                else:
                    self.currentTime = readStart
            return []
        
        # DB returns ordered results - no Python sorting needed
        # Reverse array for backward playback (architecture: emit in reverse)
        if self.rate < 0:
            events = events[::-1]
        
        # Update cursor by QUERY WINDOW, not by last event time
        # This ensures consistent pacing regardless of event clustering within window
        if self.rate >= 0:
            self.currentTime = readEnd  # Move to end of query window
        else:
            self.currentTime = readStart  # Move to start of query window (going backward)
        
        # Store cursor and window for pacing + follower sync
        self.lastEmittedCursor = self.currentTime
        self.lastQueryWindowUs = actualWindowUs
        self.lastWindow = (readStart, readEnd)  # For followers to query same window
        
        return events
    
    async def _pacedDelay(self, queryWindowUs: int):
        """
        Server-paced delay based on query window size (fixed ~1 second).
        
        Architecture: delay = queryWindow / rate
        
        Using query window instead of event span ensures consistent pacing
        regardless of data density. Each chunk represents ~1 second of timeline,
        so delay is ~1 second at rate=1.0.
        """
        if queryWindowUs == 0 or abs(self.rate) < 0.001:
            return
        
        # Architecture: Natural pacing - delay = timeline window / rate
        # 2x rate = half delay, 0.5x rate = double delay
        delaySec = (queryWindowUs / 1_000_000) / abs(self.rate)
        
        if delaySec > 0.001:
            await asyncio.sleep(delaySec)
    
    def cancel(self):
        """Stop streaming"""
        self.running = False


class OutputStreamCursor:
    """
    Follower cursor for output streams (TCP/UDP/WS).
    
    Event-driven architecture:
    - When BOUND: Waits for leader's cursorAdvancedEvent, queries leader.lastWindow
    - When UNBOUND: Live-follow mode (query from now, wait for new data notification)
    
    This ensures zero drift: followers always query the exact same time window as leader.
    """
    
    def __init__(self, connId: str, filters: Dict[str, Any], database: Database,
                 leaderConnId: Optional[str] = None, streamingManager: 'StreamingManager' = None):
        self.connId = connId
        self.filters = filters
        self.database = database
        self.leaderConnId = leaderConnId
        self.streamingManager = streamingManager
        self.log = getLogger()
        
        self.running = True
        self.newDataEvent = asyncio.Event()  # For LIVE/unbound wake on ingest
        self.followerCursor = None  # Track where follower last read (prevents data loss)
        
        # Convert string lanes to Lane enums if present
        self.lanes = None
        if filters.get('lanes'):
            self.lanes = [Lane(l) if isinstance(l, str) else l for l in filters['lanes']]
    
    async def streamChunks(self, chunkQueue: asyncio.Queue):
        """
        Stream chunks following leader timeline (bound) or live (unbound).
        
        Event-driven: waits for leader signal, queries same window, emits.
        """
        self.log.info(f"[OutputCursor] Started: conn={self.connId}, "
                     f"leader={self.leaderConnId or 'LIVE'}, filters={self.filters}")
        
        try:
            if self.leaderConnId and self.streamingManager:
                # BOUND mode: wait for leader events
                await self._streamBound(chunkQueue)
            else:
                # UNBOUND mode: live-follow
                await self._streamUnbound(chunkQueue)
                
        except asyncio.CancelledError:
            self.log.info(f"[OutputCursor] Canceled: conn={self.connId}")
            raise
        except Exception as e:
            self.log.error(f"[OutputCursor] Error: {e}", exc_info=True)
            raise
    
    async def _streamBound(self, chunkQueue: asyncio.Queue):
        """
        Bound streaming: follower tracks own cursor, catches up to leader.
        
        Uses leader.lastEmittedCursor as target, queries [followerCursor, leaderCursor]
        to catch ALL events in own lane. asyncio.Event may coalesce multiple leader
        advances — querying the full gap ensures zero data loss.
        """
        followerCursor = None  # Set on first leader signal
        
        while self.running:
            leader = self.streamingManager.getLeaderCursor(self.leaderConnId)
            if not leader:
                self.log.info(f"[OutputCursor] Leader disconnected, stopping")
                self.running = False
                break
            
            # Wait for leader to advance cursor
            try:
                await asyncio.wait_for(leader.cursorAdvancedEvent.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            
            leader.cursorAdvancedEvent.clear()
            
            leaderCursor = leader.lastEmittedCursor
            if leaderCursor is None:
                continue
            
            # Initialize follower cursor on first signal
            if followerCursor is None:
                followerCursor = leaderCursor
                continue
            
            # Query from follower's last position to leader's current position
            # This catches the FULL range regardless of coalesced signals
            if leader.rate >= 0:
                readStart, readEnd = followerCursor, leaderCursor
            else:
                readStart, readEnd = leaderCursor, followerCursor
            
            if readStart >= readEnd:
                followerCursor = leaderCursor
                continue
            
            events = await self._queryEvents(readStart, readEnd)
            followerCursor = leaderCursor
            
            if events:
                if leader.rate < 0:
                    events = events[::-1]
                chunk = StreamChunk(
                    playbackRequestId=self.connId,
                    events=events,
                    timestamp=leaderCursor,
                    complete=False
                )
                await chunkQueue.put(chunk)
    
    async def _streamUnbound(self, chunkQueue: asyncio.Queue):
        """
        Unbound streaming: live-follow mode, start from now and stream forward.
        
        Streams from NOW forward, advancing cursor with each query window.
        """
        # Start cursor at NOW (no lookback for non-UI streams)
        cursorUs = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
        
        while self.running:
            nowUs = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
            
            # Query from cursor forward to now
            readStart = cursorUs
            readEnd = nowUs
            
            events = await self._queryEvents(readStart, readEnd)
            
            if events:
                chunk = StreamChunk(
                    playbackRequestId=self.connId,
                    events=events,
                    timestamp=readEnd,
                    complete=False
                )
                await chunkQueue.put(chunk)
                cursorUs = readEnd  # Advance cursor
            else:
                # No data yet - advance cursor to now and wait for new data
                cursorUs = nowUs
                try:
                    await asyncio.wait_for(self.newDataEvent.wait(), timeout=1.0)
                    self.newDataEvent.clear()
                except asyncio.TimeoutError:
                    pass
    
    async def _queryEvents(self, startUs: int, endUs: int) -> List[Dict[str, Any]]:
        """Query events with own filters in given time window"""
        startTime = datetime.fromtimestamp(startUs / 1_000_000, tz=timezone.utc)
        endTime = datetime.fromtimestamp(endUs / 1_000_000, tz=timezone.utc)
        
        from nova.core.contract import Timebase
        
        return await asyncio.to_thread(
            self.database.queryEvents,
            startTime=startTime.isoformat(),
            stopTime=endTime.isoformat(),
            timebase=Timebase.CANONICAL,
            lanes=self.lanes,
            systemId=self.filters.get('systemId'),
            containerId=self.filters.get('containerId'),
            uniqueId=self.filters.get('uniqueId'),
            messageType=self.filters.get('messageType'),
            limit=1000
        )
    
    def cancel(self):
        """Stop streaming"""
        self.running = False


class StreamingManager:
    """
    Manages active streaming cursors for all client connections.
    
    Supports two types of streams:
    1. UI timeline cursors (leader): Run pacing loop, advance currentTime
    2. Output stream cursors (follower): Follow leader's currentTime, query with own filters
    
    Stateless: cursors are ephemeral, discarded on disconnect.
    Receives notifications from Ingest for push-based LIVE streaming.
    """
    
    def __init__(self, database: Database, config: dict = None):
        self.database = database
        self.config = config or {}
        self.log = getLogger()
        
        # Active streams: clientConnId → StreamCursor
        self.activeStreams: Dict[str, StreamCursor] = {}
        
        # Stream tasks: clientConnId → asyncio.Task
        self.streamTasks: Dict[str, asyncio.Task] = {}
        
        # Output stream cursors: connId → OutputStreamCursor
        self.outputStreams: Dict[str, OutputStreamCursor] = {}
        self.outputTasks: Dict[str, asyncio.Task] = {}
    
    def getLeaderCursor(self, leaderConnId: str) -> Optional[StreamCursor]:
        """Get a leader cursor's current state for followers to read"""
        return self.activeStreams.get(leaderConnId)
    
    def notifyNewEvent(self, event, canonicalTruthTime: str):
        """
        Notify active LIVE streams of new ingested event.
        Called synchronously from Ingest after DB write.
        Wakes up LIVE cursors (stopTime=None) to push new data immediately.
        """
        # Wake up all LIVE streams (non-blocking, just set event flags)
        liveCount = 0
        for clientConnId, cursor in self.activeStreams.items():
            if cursor.stopTime is None:  # LIVE mode only
                if hasattr(cursor, 'newDataEvent'):
                    cursor.newDataEvent.set()  # Wake up cursor to read new data
                    liveCount += 1
        
        if liveCount > 0:
            self.log.debug(f"[StreamMgr] Notified {liveCount} LIVE cursor(s) at {canonicalTruthTime}")
        
        # Wake up unbound output streams (live-follow mode)
        for connId, cursor in self.outputStreams.items():
            if cursor.leaderConnId is None:  # Unbound = LIVE mode
                if hasattr(cursor, 'newDataEvent'):
                    cursor.newDataEvent.set()
    async def startStream(self, request: StreamRequest, chunkQueue: asyncio.Queue):
        """
        Start new stream for client connection.
        Cancels any existing stream for this clientConnId.
        """
        clientConnId = request.clientConnId
        
        # Cancel existing stream if present
        await self.cancelStream(clientConnId)
        
        cursor = StreamCursor(request, self.database, self.config)
        self.activeStreams[clientConnId] = cursor
        
        # Start streaming task
        task = asyncio.create_task(cursor.streamChunks(chunkQueue))
        self.streamTasks[clientConnId] = task
        
        self.log.info(f"[StreamMgr] Started stream for conn={clientConnId}, "
                     f"playbackId={request.playbackRequestId}")
        
        return task
    
    async def cancelStream(self, clientConnId: str):
        """Cancel active stream for client connection"""
        cursor = self.activeStreams.get(clientConnId)
        if cursor:
            cursor.cancel()
            self.activeStreams.pop(clientConnId, None)
        
        task = self.streamTasks.get(clientConnId)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self.streamTasks.pop(clientConnId, None)
        self.log.info(f"[StreamMgr] Canceled stream for conn={clientConnId}")
    
    async def startOutputStream(self, connId: str, filters: Dict[str, Any], 
                                 chunkQueue: asyncio.Queue, boundInstanceId: Optional[str] = None):
        """
        Start output stream cursor (TCP/UDP/WS).
        
        Uses same streaming algorithm as UI timeline, but:
        - When bound: follows leader's currentTime
        - When unbound: live-follow mode
        """
        # Cancel any existing output stream for this connection
        await self.cancelOutputStream(connId)
        
        cursor = OutputStreamCursor(
            connId=connId,
            filters=filters,
            database=self.database,
            leaderConnId=boundInstanceId,
            streamingManager=self
        )
        self.outputStreams[connId] = cursor
        
        # Start streaming task
        task = asyncio.create_task(cursor.streamChunks(chunkQueue))
        self.outputTasks[connId] = task
        
        self.log.info(f"[StreamMgr] Started output stream: conn={connId}, "
                     f"bound={boundInstanceId or 'LIVE'}, filters={filters}")
        
        return task
    
    async def cancelOutputStream(self, connId: str):
        """Cancel active output stream"""
        cursor = self.outputStreams.get(connId)
        if cursor:
            cursor.cancel()
            self.outputStreams.pop(connId, None)
        
        task = self.outputTasks.get(connId)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self.outputTasks.pop(connId, None)
        self.log.info(f"[StreamMgr] Canceled output stream: conn={connId}")
    
    async def shutdown(self):
        """Cancel all active streams"""
        self.log.info(f"[StreamMgr] Shutting down {len(self.activeStreams)} active streams, "
                     f"{len(self.outputStreams)} output streams")
        
        for clientConnId in list(self.activeStreams.keys()):
            await self.cancelStream(clientConnId)
        
        for connId in list(self.outputStreams.keys()):
            await self.cancelOutputStream(connId)
