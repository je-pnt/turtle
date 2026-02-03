# -*- coding: utf-8 -*-
"""
MapVisualization - Real-time 3D Asset Tracking
Created by and property of Uncompromising Sensor Support LLC
"""

# Imports
import asyncio, json, quart
from typing import Dict, Any, Set, Optional, List, Tuple
from datetime import datetime, timezone


# Class
class Asset:
    
    def __init__(self, assetId: str, name: str, lat: float, lon: float, alt: float,
                 color: Tuple[int, int, int] = (255, 0, 0), 
                 model: str = "/static/data/models/Penguin.glb"):
        
        self.assetId = assetId
        self.name = name
        self.lat, self.lon, self.alt = float(lat), float(lon), float(alt)
        self.color = self.normalizeColor(color)
        self.model = model
        self.timestamp = datetime.now(timezone.utc)
    
    @staticmethod
    def normalizeColor(color: Tuple[int, int, int]) -> List[int]:
        return [int(max(0, min(255, c))) for c in color]
    

    def updatePosition(self, lat: float, lon: float, alt: float) -> None:
        self.lat, self.lon, self.alt = float(lat), float(lon), float(alt)
        self.timestamp = datetime.now(timezone.utc)
    
    def toDict(self) -> Dict[str, Any]:
        return {"assetId": self.assetId, "assetName": self.name, "time": self.timestamp.isoformat().replace("+00:00", "Z"),
                "lat": self.lat, "lon": self.lon, "alt": self.alt, "color": self.color, "modelUrl": self.model}


class MapVisualization:
    
    # Hardware-optimized defaults for fast, smooth visualization
    DEFAULT_BATCH_SIZE = 25
    DEFAULT_BATCH_WINDOW = 0.1  # 10 Hz update rate
    DEFAULT_QUEUE_MAX_SIZE = 200
    DEFAULT_WEBSOCKET_KEEPALIVE = 30
    
    # Client-side smoothing parameters (sent to browser)
    DEFAULT_INTERPOLATION_DEGREE = 1  # Linear interpolation for GNSS data
    DEFAULT_EXTRAPOLATION_DURATION = 1.2  # Smooth gaps up to 1.2 seconds
    DEFAULT_CLOCK_LAG = 1.0  # 1-second lag for smooth motion
    DEFAULT_TRAIL_TIME = 120  # 2-minute trails
    
    def __init__(self, log=None,
                 # Model/color defaults
                 defaultModel: str = "/static/data/models/Penguin.glb",
                 defaultColor: Tuple[int, int, int] = (255, 0, 0),
                 # Performance tuning (camelCase for consistency)
                 batchSize: int = DEFAULT_BATCH_SIZE,
                 batchWindow: float = DEFAULT_BATCH_WINDOW,
                 queueMaxSize: int = DEFAULT_QUEUE_MAX_SIZE,
                 websocketKeepalive: int = DEFAULT_WEBSOCKET_KEEPALIVE,
                 # Smoothing parameters
                 interpolationDegree: int = DEFAULT_INTERPOLATION_DEGREE,
                 extrapolationDuration: float = DEFAULT_EXTRAPOLATION_DURATION,
                 clockLag: float = DEFAULT_CLOCK_LAG,
                 trailTime: int = DEFAULT_TRAIL_TIME):
        
        self.log = log
        self.assets: Dict[str, Asset] = {}
        self.websockets: Set = set()
        self.updateQueue: Optional[asyncio.Queue] = None
        self.drainTask: Optional[asyncio.Task] = None
        self.queueDrops, self.totalUpdates, self.totalBroadcasts = 0, 0, 0

        # Instance config (camelCase, hardware-optimized)
        self.defaultModel = defaultModel
        self.defaultColor = tuple(Asset.normalizeColor(defaultColor))

        self.batchSize = int(batchSize)
        self.batchWindow = float(batchWindow)
        self.queueMaxSize = int(queueMaxSize)
        self.websocketKeepalive = int(websocketKeepalive)
        
        # Smoothing configuration (sent to client)
        self.interpolationDegree = int(interpolationDegree)
        self.extrapolationDuration = float(extrapolationDuration)
        self.clockLag = float(clockLag)
        self.trailTime = int(trailTime)

    
    async def start(self) -> None:

        # Create update queue
        if self.updateQueue is None:
            self.updateQueue = asyncio.Queue(maxsize=self.queueMaxSize)
        
        # Start drain task
        if self.drainTask is None or self.drainTask.done():
            self.drainTask = asyncio.create_task(self.drainUpdateQueue(), name='mapVisualizationDrain')
    

    async def stop(self) -> None:
        
        # Stop drain task
        if self.drainTask and not self.drainTask.done():
            self.drainTask.cancel()
            try:
                await self.drainTask
            except asyncio.CancelledError:
                pass
        
        # Close websockets
        for ws in list(self.websockets):
            self.websockets.discard(ws)
    

    def updateAsset(self, assetId: str, lat: float, lon: float, alt: float,
                    name: Optional[str] = None,
                    color: Optional[Tuple[int, int, int]] = None,
                    model: Optional[str] = None) -> None:
        
        # Normalize name
        if name is None:
            name = assetId

        # Resolve defaults (do not mutate defaults)
        resolvedColor = Asset.normalizeColor(self.defaultColor) if color is None else Asset.normalizeColor(color)
        resolvedModel = self.defaultModel if model is None else model

        # Update or create asset
        if assetId in self.assets:
            asset = self.assets[assetId]
            asset.updatePosition(lat, lon, alt)
            asset.color = resolvedColor
            asset.model = resolvedModel
        else:
            asset = Asset(assetId, name, lat, lon, alt, tuple(resolvedColor), resolvedModel)
            self.assets[assetId] = asset
        
        # Queue update
        self.queueUpdate(asset)
        self.totalUpdates += 1
    

    def removeAsset(self, assetId: str) -> bool:
        if assetId in self.assets:
            del self.assets[assetId]
            self.queueRemoval(assetId)
            return True
        return False
    

    def getAsset(self, assetId: str) -> Optional[Asset]:
        return self.assets.get(assetId)
    

    def getAllAssets(self) -> Dict[str, Asset]:
        return self.assets.copy()
    

    def clearAssets(self) -> None:
        self.assets.clear()
    

    def queueUpdate(self, asset: Asset) -> None:

        # Handle no queue
        if self.updateQueue is None:
            return
        
        # Queue update
        try:
            self.updateQueue.put_nowait({"type": "update", "asset": asset.toDict()})
        except asyncio.QueueFull:
            try:
                self.updateQueue.get_nowait()
                self.updateQueue.put_nowait({"type": "update", "asset": asset.toDict()})
                self.queueDrops += 1
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
    

    def queueRemoval(self, assetId: str) -> None:

        # Handle no queue
        if self.updateQueue is None:
            return
        
        # Queue removal
        try:
            self.updateQueue.put_nowait({"type": "remove", "assetId": assetId})
        except asyncio.QueueFull:
            pass
    
    async def drainUpdateQueue(self) -> None:
        batch = []
        
        while True:
            await asyncio.sleep(self.batchWindow)
            try:
                while not self.updateQueue.empty() and len(batch) < self.batchSize:
                    update = self.updateQueue.get_nowait()
                    batch.append(update)
                
                if batch:
                    if len(batch) == 1:
                        await self.broadcast(batch[0])
                    else:
                        await self.broadcast({"type": "batch", "updates": batch})
                    batch.clear()
                    self.totalBroadcasts += 1
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logError(f"Error in map update queue drain: {e}")
    

    async def broadcast(self, message: Dict[str, Any]) -> None:

        # Handle no websockets
        if not self.websockets:
            return
        
        # Get payload and init disconnected
        payload = json.dumps(message)
        disconnected = []
        
        # Broadcast to all websockets
        for ws in list(self.websockets):
            try:
                await ws.send(payload)
            except Exception:
                disconnected.append(ws)
        
        # Remove disconnected websockets
        for ws in disconnected:
            self.websockets.discard(ws)
    

    async def handleWebsocketConnection(self, ws) -> None:

        # Add to active websockets
        try:
            self.websockets.add(ws)
            
            # Send config snapshot with smoothing parameters for client setup
            configSnapshot = {
                "type": "config",
                "interpolationDegree": self.interpolationDegree,
                "extrapolationDuration": self.extrapolationDuration,
                "clockLag": self.clockLag,
                "trailTime": self.trailTime
            }
            await ws.send(json.dumps(configSnapshot))
            
            # Send asset snapshot
            snapshot = {"type": "snapshot", "assets": [asset.toDict() for asset in self.assets.values()]}
            await ws.send(json.dumps(snapshot))
            
            # Keep alive
            while True:
                await asyncio.sleep(self.websocketKeepalive)
        
        # Handle exceptions
        except Exception as e:
            self.logError(f"Map WebSocket error: {e}")
        finally:
            self.websockets.discard(ws)
    

    def registerWebsocket(self, app, websocketRoute: str = '/ws-map'):
        """Register websocket handler for map updates (host provides HTML/JS/CSS)"""
        @app.websocket(websocketRoute)
        async def mapWebsocket():
            ws = quart.websocket._get_current_object()
            await self.handleWebsocketConnection(ws)
    
    def registerIngestEndpoint(self, app, ingestRoute: str = '/ingest'):
        """Register REST endpoint for external asset ingestion"""
        @app.post(ingestRoute)
        async def ingestAsset():
            try:
                payload = await quart.request.get_json(force=True)
                self.ingestExternalAsset(payload)
                return quart.jsonify({"ok": True})
            except Exception as e:
                return quart.jsonify({"ok": False, "error": str(e)}), 400
    

    def validateExternalPayload(self, payload: Dict[str, Any]) -> Dict[str, Any]:

        # Validate the payload data fields
        required = ["assetId", "assetName", "time", "lat", "lon", "alt", "color"]
        for field in required:
            if field not in payload:
                raise ValueError(f"Missing required field: {field}")
        
        # Normalize time
        t = payload["time"]
        if isinstance(t, str):
            if t.endswith("Z"):
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(t)
        elif isinstance(t, (int, float)):
            dt = datetime.fromtimestamp(float(t), tz=timezone.utc)
        else:
            raise ValueError("time must be ISO8601 string or Unix timestamp")
        payload["time"] = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Normalize color
        color = payload["color"]
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError("color must be [r,g,b] array")
        payload["color"] = [int(max(0, min(255, c))) for c in color]
        
        if "modelUrl" in payload and not isinstance(payload["modelUrl"], str):
            raise ValueError("modelUrl must be a string")
        
        return payload
    

    def ingestExternalAsset(self, payload: Dict[str, Any]) -> None:
        validated = self.validateExternalPayload(payload)
        self.updateAsset(
            assetId=validated["assetId"],
            lat=validated["lat"],
            lon=validated["lon"],
            alt=validated["alt"],
            name=validated.get("assetName"),
            color=tuple(validated["color"]),
            model=validated.get("modelUrl", self.defaultModel)
        )
    

    def getMetrics(self) -> Dict[str, Any]:
        return {"totalAssets": len(self.assets),"connectedClients": len(self.websockets),"queueDrops": self.queueDrops,
            "totalUpdates": self.totalUpdates,"totalBroadcasts": self.totalBroadcasts,"queueSize": self.updateQueue.qsize() if self.updateQueue else 0}
    

    def logError(self, message: str) -> None:
        if self.log:
            self.log.error(message, component='MapVisualization')
        else:
            print(f"[MapVisualization ERROR] {message}")
