# GEM Architecture

**GNSS Equipment Middleware - Complete Implementation Documentation**  
**Version:** 2.0  
**Date:** January 25, 2026  
**Status:** Production

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture](#system-architecture)
3. [Hardware Integration](#hardware-integration)
4. [Message Parsing](#message-parsing)
5. [Metadata Management](#metadata-management)
6. [Command Execution](#command-execution)
7. [Configuration Management](#configuration-management)
8. [Multi-Lane Publishing](#multi-lane-publishing)
9. [Error Handling & Recovery](#error-handling--recovery)
10. [Testing & Validation](#testing--validation)

---

## Executive Summary

**GEM (GNSS Equipment Middleware)** is the integration layer between raw hardware devices and the NOVA archive. It:

1. **Discovers** devices via hardwareService topology
2. **Parses** raw protocol bytes (UBX, SBF, NMEA) into typed messages
3. **Publishes** three lanes: raw (TCP replay), truth (10 Hz), UI (1-2 Hz)
4. **Manages** metadata lifecycle (connect, disconnect, online status)
5. **Executes** manifest-driven commands (hotStart, coldStart, uploadConfig)
6. **Applies** configurations via hardwareService REQ/REP

**Core Principles**:
- **Producer Authority**: GEM assigns assetId, containerId, scopeId
- **Change-Only Metadata**: Publish on connect + change (not periodic)
- **Deterministic Publishing**: Alphabetically sorted JSON, stable hashing
- **Command Manifests**: JSON-defined actions with IPC fallback
- **Multi-Parser Architecture**: Protocol-agnostic plugin system
- **Transport Abstraction**: Uses `/transport` (from `/sdk`) for all messaging (IPC locally, NATS for remote)

---

## System Architecture

### Service Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│                             GEM                                  │
│                                                                   │
│  ┌───────────────┐   ┌────────────────┐   ┌──────────────────┐ │
│  │ Topology      │   │ Message Parser │   │ Command Router   │ │
│  │ Subscriber    │   │                │   │                  │ │
│  └───────┬───────┘   └────────┬───────┘   └────────┬─────────┘ │
│          │                     │                     │           │
│  ┌───────▼─────────────────────▼─────────────────────▼────────┐ │
│  │              Device Manager (AssetRepository)              │ │
│  └──────┬──────────────────┬──────────────────┬───────────────┘ │
│         │                  │                  │                  │
│  ┌──────▼─────┐   ┌────────▼──────┐   ┌──────▼────────┐        │
│  │ Raw Lane   │   │ Truth Lane    │   │ UI Lane       │        │
│  │ (native)   │   │ (10 Hz)       │   │ (1-2 Hz)      │        │
│  └──────┬─────┘   └────────┬──────┘   └──────┬────────┘        │
└─────────┼──────────────────┼──────────────────┼─────────────────┘
          │                  │                  │
          └──────────────────┴──────────────────┴──> NATS
```

### File Structure

```
gem/
├── gemService.py              # Main entry point, orchestration
├── gem.manifest.json          # Command definitions
├── config.json                # Service config (transport URI, scopes)
├── core/
│   ├── assetRepository.py     # Device registry, metadata tracking
│   ├── commandHandler.py      # Manifest-driven command execution
│   ├── hardwareServiceClient.py  # REQ/REP control channel
│   ├── transportManager.py    # Transport abstraction wrapper
│   └── devices/
│       ├── gnssReceiver.py    # GNSS device abstraction
│       └── baseDevice.py      # Device base class
├── parsers/
│   ├── ubxParser.py           # UBX protocol (u-blox)
│   ├── sbfParser.py           # SBF protocol (Septentrio)
│   ├── nmeaParser.py          # NMEA protocol (generic)
│   └── parserFactory.py       # Parser selection
├── drivers/                   # * Driver architecture (proposed)
│   ├── baseDriver.py          # Base class for all drivers
│   ├── streamDriver.py        # Stream ingest + file writing + export
│   └── commandAdapter.py      # Command encoding + ack parsing
└── test/
    ├── test_device_integration.py
    ├── test_ipc.py
    └── testPhase4Commands.py
```

---

## Transport Usage

### Overview

GEM uses **`/transport`** (from `/sdk`) for all messaging, supporting multiple underlying bindings:

**Local chain** (hardwareService → GEM):  
- URI: `nng+ipc:///tmp/hwService` (or `ipc://` scheme)
- Protocol: NNG connectionless pub/sub + REQ/REP

**Network chain** (GEM → novaArchive):  
- URI: `nats://localhost:4222` (or remote NATS server)
- Protocol: NATS distributed pub/sub

**UI flows** (future):  
- Desired direction: Move HTTP/WebSocket behind `/transport` over time

### Initialization

```python
from sdk.transport import createTransport

class GemService:
    def __init__(self, config):
        self.config = config
        
        # Create transport from URI
        hw_uri = config.get('hardwareServiceUri', 'nng+ipc:///tmp/hwService')
        archive_uri = config.get('archiveUri', 'nats://localhost:4222')
        
        self.hw_transport = createTransport(hw_uri)
        self.archive_transport = createTransport(archive_uri)
    
    async def start(self):
        # Connect to hardwareService (IPC)
        await self.hw_transport.connect(
            self.config.get('hardwareServiceUri', 'nng+ipc:///tmp/hwService'),
            ipcDir='/tmp'
        )
        
        # Connect to novaArchive (NATS)
        await self.archive_transport.connect(
            self.config.get('archiveUri', 'nats://localhost:4222'),
            name='gem-service',
            maxReconnectAttempts=60
        )
        
        # Subscribe to topology events
        await self.hw_transport.subscribe(
            f'hardwareService.events.{self.container_id}',
            self.handle_topology
        )
        
        # Subscribe to commands
        await self.archive_transport.subscribe(
            f'command.*.{self.asset_id}',
            self.handle_command
        )
```

### Publishing Streams

```python
async def publish_truth_message(self, device, parsed_msg):
    # Build envelope
    envelope = {
        "assetId": device.asset_id,
        "scopeId": device.scope_id,
        "streamType": parsed_msg['messageType'],
        "sequenceNum": device.get_next_sequence_num(parsed_msg['messageType']),
        "timestampMs": int(time.time() * 1000),
        "patch": parsed_msg['data'],
        "version": 1
    }
    
    # Publish via transport (abstracted from NATS/IPC details)
    await self.archive_transport.publish(
        f"stream.truth.{parsed_msg['messageType']}.{device.scope_id}.{device.asset_id}",
        json.dumps(envelope, sort_keys=True).encode()
    )
```

### REQ/REP Pattern

```python
async def request_topology(self):
    # Send request via transport
    request = {"command": "getTopology"}
    
    response_bytes = await self.hw_transport.request(
        f'hardwareService.control.{self.container_id}',
        json.dumps(request).encode(),
        timeout=5.0
    )
    
    topology = json.loads(response_bytes.decode())
    return topology
```

---

## Driver Architecture *

**Current approach** (open to change): Drivers can be split into two classes to handle different responsibilities.

### BaseDriver

**Base class** for all drivers (stream and command):

```python
class BaseDriver(ABC):
    \"\"\"Base class for all drivers (stream ingest, file writing, command encoding).\"\"\"
    
    def __init__(self, config: dict):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @abstractmethod
    async def start(self):
        \"\"\"Initialize driver resources.\"\"\"
        pass
    
    @abstractmethod
    async def stop(self):
        \"\"\"Cleanup driver resources.\"\"\"
        pass
    
    @abstractmethod
    def validate_config(self) -> bool:
        \"\"\"Validate driver configuration.\"\"\"
        pass
```

### StreamDriver

**Handles ingest + file writing + export**:

```python
class StreamDriver(BaseDriver):
    \"\"\"Driver for stream ingest, cold file writing, and export.\"\"\"
    
    @abstractmethod
    async def ingest(self, message: dict):
        \"\"\"Receive message from transport and process.\"\"\"
        pass
    
    @abstractmethod
    async def write(self):
        \"\"\"Write buffered messages to cold file (daily rotation).\"\"\"
        pass
    
    @abstractmethod
    async def export(self, start_time: int, stop_time: int, output_path: str):
        \"\"\"Export messages in time window to file.\"\"\"
        pass
```

**Example: RawBinaryDriver** (for .bin files):

```python
class RawBinaryDriver(StreamDriver):
    def __init__(self, config: dict):
        super().__init__(config)
        self.buffer = []
        self.current_file = None
        self.current_date = None
    
    async def ingest(self, message: dict):
        # Buffer raw bytes
        if message['lane'] == 'raw':
            self.buffer.append(message)
            
            # Flush if buffer full
            if len(self.buffer) >= 1000:
                await self.write()
    
    async def write(self):
        # Check date rotation
        today = datetime.now().strftime('%Y-%m-%d')
        if self.current_date != today:
            if self.current_file:
                self.current_file.close()
            
            # Open new daily file
            file_path = f"storage/{today}/{self.asset_id}.bin"
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            self.current_file = open(file_path, 'ab')
            self.current_date = today
        
        # Write buffered messages
        for msg in self.buffer:
            payload_bytes = bytes.fromhex(msg['payload'])
            self.current_file.write(payload_bytes)
        
        self.buffer.clear()
    
    async def export(self, start_time: int, stop_time: int, output_path: str):
        # Query messages from DB (or cold files)
        messages = await self.db.query(
            'SELECT payload FROM messages WHERE lane="raw" AND timestampMs BETWEEN ? AND ?',
            (start_time, stop_time)
        )
        
        # Write to export file
        with open(output_path, 'wb') as f:
            for msg in messages:
                f.write(bytes.fromhex(msg['payload']))
```

### CommandAdapter

**Handles command→bytes and optional ack parsing**:

```python
class CommandAdapter(BaseDriver):
    \"\"\"Driver for command encoding, transmission, and acknowledgment parsing.\"\"\"
    
    @abstractmethod
    async def encode_command(self, verb: str, params: dict) -> bytes:
        \"\"\"Convert command verb + params to raw bytes.\"\"\"
        pass
    
    @abstractmethod
    async def parse_ack(self, raw_bytes: bytes) -> dict:
        \"\"\"Parse acknowledgment from device (optional).\"\"\"
        pass
    
    @abstractmethod
    async def validate_scope(self, scope_id: str) -> bool:
        \"\"\"Validate command is authorized for scope.\"\"\"
        pass
```

**Example: UbxCommandAdapter**:

```python
class UbxCommandAdapter(CommandAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self.allowed_scopes = config.get('allowedScopes', [])
    
    async def encode_command(self, verb: str, params: dict) -> bytes:
        if verb == 'receiver.hotStart':
            # Build UBX-CFG-RST (hot start)
            payload = struct.pack('<HBB', 0x0000, 0x09, 0x00)
            return self._build_ubx_message(0x06, 0x04, payload)
        
        elif verb == 'receiver.coldStart':
            # Build UBX-CFG-RST (cold start)
            payload = struct.pack('<HBB', 0xFFFF, 0x09, 0x00)
            return self._build_ubx_message(0x06, 0x04, payload)
        
        elif verb == 'receiver.uploadConfig':
            # Return config bytes directly (already in params)
            return bytes.fromhex(params['configData'])
        
        else:
            raise ValueError(f"Unknown command verb: {verb}")
    
    async def parse_ack(self, raw_bytes: bytes) -> dict:
        # Parse UBX-ACK-ACK or UBX-ACK-NAK
        if len(raw_bytes) < 10:
            return {"status": "error", "message": "Invalid ACK length"}
        
        msg_class = raw_bytes[2]
        msg_id = raw_bytes[3]
        
        if msg_class == 0x05 and msg_id == 0x01:  # ACK-ACK
            return {"status": "success", "message": "Command acknowledged"}
        elif msg_class == 0x05 and msg_id == 0x00:  # ACK-NAK
            return {"status": "error", "message": "Command rejected"}
        else:
            return {"status": "unknown", "message": "Non-ACK message"}
    
    async def validate_scope(self, scope_id: str) -> bool:
        return scope_id in self.allowed_scopes
```

### Driver Registration *

**Proposed pattern**:

```python
# In gemService.py
class GemService:
    def __init__(self, config):
        self.stream_drivers = {}
        self.command_adapters = {}
        
        # Register drivers
        self.register_stream_driver('raw', RawBinaryDriver(config))
        self.register_stream_driver('csv', CsvStreamDriver(config))
        self.register_command_adapter('ubx', UbxCommandAdapter(config))
        self.register_command_adapter('sbf', SbfCommandAdapter(config))
    
    def register_stream_driver(self, lane: str, driver: StreamDriver):
        self.stream_drivers[lane] = driver
    
    def register_command_adapter(self, protocol: str, adapter: CommandAdapter):
        self.command_adapters[protocol] = adapter
    
    async def publish_message(self, device, parsed_msg):
        # Route to appropriate stream driver
        for lane, driver in self.stream_drivers.items():
            await driver.ingest(parsed_msg)
    
    async def execute_command(self, device, verb, params):
        # Route to appropriate command adapter
        adapter = self.command_adapters.get(device.protocol)
        if not adapter:
            raise ValueError(f"No adapter for protocol: {device.protocol}")
        
        # Encode command
        command_bytes = await adapter.encode_command(verb, params)
        
        # Send via hardwareService
        await self.hw_transport.request(
            f'hardwareService.control.{self.container_id}',
            json.dumps({
                "command": "applyConfig",
                "deviceId": device.asset_id,
                "configBytes": list(command_bytes)
            }).encode()
        )
```

---

## Hardware Integration

### Topology Discovery

**Flow**:
1. GEM starts, connects to NATS
2. GEM publishes to `hardwareService.control.{containerId}` with `{"command": "getTopology"}`
3. hardwareService replies with topology (devices + subjects)
4. GEM subscribes to `hardwareService.events.{containerId}` for topology updates
5. GEM subscribes to device streams: `device.raw.{deviceId}.{kind}.{dataKind}`

**Topology Message** (from hardwareService):
```json
{
  "event": "topology",
  "containerId": "Payload",
  "devices": [
    {
      "deviceId": "8220-F9P",
      "kind": "ubx",
      "subject": "device.raw.8220-F9P.ubx.serial",
      "metadata": {
        "port": "COM3",
        "baudRate": 230400
      }
    }
  ]
}
```

**Device Connect** (GEM):
```python
def handle_topology(msg):
    for device_info in msg['devices']:
        device_id = device_info['deviceId']
        kind = device_info['kind']
        subject = device_info['subject']
        
        # Create parser
        parser = parser_factory.create_parser(kind)
        
        # Create device abstraction
        device = GnssReceiver(
            asset_id=device_id,
            scope_id=self.scope_id,
            container_id=self.container_id,
            parser=parser
        )
        
        # Register in asset repository
        self.asset_repo.register_device(device)
        
        # Subscribe to raw stream
        await self.nats.subscribe(subject, self.handle_raw_bytes)
        
        # Publish metadata (online=False until first message)
        await self.publish_metadata(device)
```

### Raw Stream Processing

**Flow**:
1. hardwareService publishes binary to `device.raw.{deviceId}.{kind}.serial`
2. GEM receives binary message
3. GEM identifies device by `deviceId` in subject
4. GEM passes bytes to parser (incremental parsing)
5. Parser yields typed messages
6. GEM publishes to all three lanes (raw, truth, UI)

**Raw Byte Handler**:
```python
async def handle_raw_bytes(msg):
    subject = msg.subject  # device.raw.8220-F9P.ubx.serial
    parts = subject.split('.')
    device_id = parts[2]  # 8220-F9P
    
    device = self.asset_repo.get_device(device_id)
    if not device:
        logger.warning(f"Unknown device: {device_id}")
        return
    
    # Raw lane (passthrough for TCP replay)
    await self.nats.publish(
        f"stream.raw.{device.scope_id}.{device.asset_id}",
        msg.data  # Binary
    )
    
    # Parse incrementally
    for parsed_msg in device.parser.parse_bytes(msg.data):
        # Truth lane (10 Hz)
        await self.publish_truth_message(device, parsed_msg)
        
        # UI lane (rate-limited 1-2 Hz)
        await self.publish_ui_message(device, parsed_msg)
```

### Device Lifecycle

**Device States**:
- `discovered`: In topology, not yet seen data
- `online`: Actively streaming data
- `offline`: No data for 30 seconds (TTL)

**Online Detection**:
```python
def handle_first_message(device):
    if not device.online:
        device.online = True
        device.last_seen = time.time() * 1000
        
        # Publish metadata update (change-only)
        await self.publish_metadata_update(device, {
            "online": True,
            "lastSeen": device.last_seen
        })
```

**Offline Detection**:
```python
async def monitor_device_health():
    while True:
        await asyncio.sleep(5)  # Check every 5 seconds
        
        for device in self.asset_repo.get_all_devices():
            if device.online:
                age = (time.time() * 1000) - device.last_seen
                if age > 30000:  # 30 seconds
                    device.online = False
                    
                    # Publish metadata update
                    await self.publish_metadata_update(device, {
                        "online": False,
                        "lastSeen": device.last_seen
                    })
```

---

## Message Parsing

### Parser Architecture

**Design**: Protocol-agnostic plugin system with incremental parsing.

**Parser Interface**:
```python
class BaseParser(ABC):
    @abstractmethod
    def parse_bytes(self, data: bytes) -> Iterator[dict]:
        """Parse raw bytes, yield typed messages"""
        pass
    
    @abstractmethod
    def reset(self):
        """Reset parser state"""
        pass
```

### UBX Parser (u-blox)

**Implementation**: `parsers/ubxParser.py`

**Message Types**:
- `UBX-NAV-PVT` (0x01 0x07): Position, velocity, time
- `UBX-NAV-SAT` (0x01 0x35): Satellite signals
- `UBX-NAV-DOP` (0x01 0x04): Dilution of precision

**Parsing Flow**:
```python
class UbxParser(BaseParser):
    def __init__(self):
        self.buffer = bytearray()
    
    def parse_bytes(self, data: bytes):
        self.buffer.extend(data)
        
        while len(self.buffer) >= 8:  # Min UBX message size
            # Find sync bytes (0xB5 0x62)
            sync_idx = self.buffer.find(b'\xB5\x62')
            if sync_idx == -1:
                self.buffer.clear()
                break
            
            # Discard junk before sync
            if sync_idx > 0:
                self.buffer = self.buffer[sync_idx:]
            
            # Parse header
            if len(self.buffer) < 6:
                break
            
            msg_class = self.buffer[2]
            msg_id = self.buffer[3]
            length = struct.unpack('<H', self.buffer[4:6])[0]
            
            # Check if full message available
            msg_len = 6 + length + 2  # header + payload + checksum
            if len(self.buffer) < msg_len:
                break
            
            # Verify checksum
            ck_a, ck_b = self._calculate_checksum(self.buffer[2:6+length])
            if self.buffer[6+length] != ck_a or self.buffer[6+length+1] != ck_b:
                logger.warning("UBX checksum failed")
                self.buffer = self.buffer[1:]  # Advance by 1 byte
                continue
            
            # Extract payload
            payload = self.buffer[6:6+length]
            
            # Parse message type
            if msg_class == 0x01 and msg_id == 0x07:  # NAV-PVT
                yield self._parse_nav_pvt(payload)
            elif msg_class == 0x01 and msg_id == 0x35:  # NAV-SAT
                yield self._parse_nav_sat(payload)
            
            # Remove parsed message
            self.buffer = self.buffer[msg_len:]
    
    def _parse_nav_pvt(self, payload):
        # Parse NAV-PVT fields
        itow = struct.unpack('<I', payload[0:4])[0]
        year = struct.unpack('<H', payload[4:6])[0]
        month = payload[6]
        day = payload[7]
        # ... (more fields)
        
        lon = struct.unpack('<i', payload[24:28])[0] * 1e-7  # deg
        lat = struct.unpack('<i', payload[28:32])[0] * 1e-7  # deg
        height = struct.unpack('<i', payload[32:36])[0] / 1000.0  # m
        
        heading = struct.unpack('<i', payload[64:68])[0] * 1e-5  # deg
        ground_speed = struct.unpack('<i', payload[60:64])[0] / 1000.0  # m/s
        
        return {
            "messageType": "position",
            "data": {
                "lat": round(lat, 8),
                "lon": round(lon, 8),
                "alt": round(height, 2),
                "heading": round(heading, 2),
                "speed": round(ground_speed, 2)
            }
        }
```

### SBF Parser (Septentrio)

**Implementation**: `parsers/sbfParser.py`

**Message Types**:
- `PVTGeodetic` (4007): Position, velocity, time
- `MeasEpoch` (4027): Satellite measurements
- `ReceiverStatus` (4014): Receiver health

**Frame Format**: `$@` sync + header (8 bytes) + payload + CRC (4 bytes)

### NMEA Parser (Generic)

**Implementation**: `parsers/nmeaParser.py`

**Message Types**:
- `$GPGGA`: GPS fix data
- `$GPRMC`: Recommended minimum data
- `$GPGSV`: Satellites in view

**Parsing**:
```python
def parse_bytes(self, data: bytes):
    lines = data.decode('ascii', errors='ignore').split('\r\n')
    
    for line in lines:
        if line.startswith('$GPGGA'):
            yield self._parse_gga(line)
        elif line.startswith('$GPGSV'):
            yield self._parse_gsv(line)

def _parse_gga(self, line):
    fields = line.split(',')
    
    # Parse latitude (DDMM.MMMM)
    lat_str = fields[2]
    lat_deg = int(lat_str[:2])
    lat_min = float(lat_str[2:])
    lat = lat_deg + lat_min / 60.0
    if fields[3] == 'S':
        lat = -lat
    
    # Parse longitude (DDDMM.MMMM)
    lon_str = fields[4]
    lon_deg = int(lon_str[:3])
    lon_min = float(lon_str[3:])
    lon = lon_deg + lon_min / 60.0
    if fields[5] == 'W':
        lon = -lon
    
    # Parse altitude
    alt = float(fields[9])
    
    return {
        "messageType": "position",
        "data": {
            "lat": round(lat, 8),
            "lon": round(lon, 8),
            "alt": round(alt, 2)
        }
    }
```

---

## Metadata Management

### Metadata Structure

**Full Metadata** (published on connect):
```json
{
  "assetId": "8220-F9P",
  "scopeId": "payload-1",
  "timestampMs": 1706188496789,
  "metadata": {
    "systemId": "mission-alpha",
    "systemDisplayName": "Mission Alpha",
    "containerId": "payload-1",
    "containerDisplayName": "Payload 1",
    "name": "ZED-F9P Receiver",
    "entityType": "gnss-receiver",
    "cardType": "gnss-card",
    "online": false,
    "lastSeen": 0,
    "attributes": {
      "manufacturer": "u-blox",
      "model": "ZED-F9P",
      "firmwareVersion": "1.32"
    },
    "priority": 0,
    "source": "producer"
  }
}
```

**Metadata Update** (published on change):
```json
{
  "assetId": "8220-F9P",
  "scopeId": "payload-1",
  "timestampMs": 1706188500000,
  "metadata": {
    "online": true,
    "lastSeen": 1706188500000
  }
}
```

### Change-Only Publishing

**Implementation**:
```python
class GnssReceiver:
    def __init__(self, asset_id, scope_id, container_id):
        self.asset_id = asset_id
        self.scope_id = scope_id
        self.container_id = container_id
        self.metadata_cache = {}  # Current state
        self.metadata_published = {}  # Last published state
    
    async def publish_metadata_update(self, updates: dict):
        # Merge updates into cache
        self.metadata_cache.update(updates)
        
        # Compute diff (what changed)
        changed_fields = {}
        for key, value in updates.items():
            if key not in self.metadata_published or self.metadata_published[key] != value:
                changed_fields[key] = value
        
        if not changed_fields:
            return  # No actual changes
        
        # Publish change-only message
        msg = {
            "assetId": self.asset_id,
            "scopeId": self.scope_id,
            "timestampMs": int(time.time() * 1000),
            "metadata": changed_fields
        }
        
        await self.nats.publish(
            f"archive.ingest.{self.scope_id}.metadata.upsert",
            json.dumps(msg, sort_keys=True).encode()
        )
        
        # Update published state
        self.metadata_published.update(changed_fields)
```

### Metadata Fields

**Required Fields**:
- `assetId`: Unique identifier (assigned by GEM)
- `scopeId`: Scope identifier (from config)
- `systemId`: System identifier (from config)
- `containerId`: Container identifier (from config or topology)
- `name`: Display name
- `entityType`: Type (gnss-receiver, sensor, etc.)
- `cardType`: UI card type (gnss-card, sensor-card, etc.)
- `online`: Boolean (true if streaming)
- `lastSeen`: Timestamp of last message (ms)

**Optional Fields**:
- `systemDisplayName`: Human-readable system name
- `containerDisplayName`: Human-readable container name
- `attributes`: Custom key-value pairs (manufacturer, model, etc.)
- `priority`: Override priority (default: 0 for producer)
- `source`: Metadata source (producer, ground, etc.)

---

## Command Execution

### Manifest-Driven Commands

**Manifest**: `gem.manifest.json`

**Structure**:
```json
{
  "actions": [
    {
      "verb": "receiver.hotStart",
      "actionId": "hotStart",
      "displayName": "Hot Start",
      "description": "Perform hot start (use existing almanac)",
      "targetType": "gnss-receiver",
      "params": []
    },
    {
      "verb": "receiver.coldStart",
      "actionId": "coldStart",
      "displayName": "Cold Start",
      "description": "Perform cold start (clear all ephemeris)",
      "targetType": "gnss-receiver",
      "params": []
    },
    {
      "verb": "receiver.uploadConfig",
      "actionId": "uploadConfig",
      "displayName": "Upload Configuration",
      "description": "Upload configuration file to receiver",
      "targetType": "gnss-receiver",
      "params": [
        {"name": "fileName", "type": "string", "required": true},
        {"name": "configData", "type": "string", "required": true}
      ]
    }
  ]
}
```

### Command Flow (Connectionless)

**Key Principles**:
- **No Session State**: Commands are routed via metadata (scopeId, assetId) with no connection tracking
- **Audit Trail**: novaArchive stores full request+result in commands table
- **Replay Safety**: Commands received during replay are blocked from hardware execution

**Flow Diagram**:

```
┌─────────┐            ┌──────────────┐            ┌──────┐            ┌─────┐
│ Browser │            │  novaArchive │            │  GEM │            │ HW  │
└────┬────┘            └──────┬───────┘            └───┬──┘            └──┬──┘
     │                        │                        │                  │
     │ POST /api/commands     │                        │                  │
     ├───────────────────────>│                        │                  │
     │                        │                        │                  │
     │                        │ Store in commands      │                  │
     │                        │ table (isReplay=false) │                  │
     │                        │                        │                  │
     │                        │ Lookup metadata        │                  │
     │                        │ (scopeId, systemId)    │                  │
     │                        │                        │                  │
     │                        │ Generate commandId     │                  │
     │                        │                        │                  │
     │                        │ Transport: command.{verb}.{entityId}      │
     │                        ├───────────────────────>│                  │
     │                        │                        │                  │
     │ 200 OK                 │                        │                  │
     │ {commandId, status}    │                        │                  │
     │<───────────────────────┤                        │                  │
     │                        │                        │                  │
     │                        │                        │ Validate scopeId │
     │                        │                        │ Check isReplay   │
     │                        │                        │ (must be false)  │
     │                        │                        │                  │
     │                        │                        │ IPC: applyConfig │
     │                        │                        ├─────────────────>│
     │                        │                        │                  │
     │                        │                        │  ACK/NAK         │
     │                        │                        │<─────────────────┤
     │                        │                        │                  │
     │                        │ archive.ingest.{scope}.command.result     │
     │                        │<───────────────────────┤                  │
     │                        │                        │                  │
     │                        │ Update commands table  │                  │
     │                        │ (status, result)       │                  │
     │                        │                        │                  │
```

**UI Progress States**:
- **Sent**: Command accepted by novaArchive, stored in DB
- **Confirmed**: GEM received command via transport
- **Progress**: GEM sent to hardwareService, waiting for ACK
- **Result**: ACK/NAK received, final status published

**Replay Safety**:

Commands are blocked from hardware execution during replay via **three layers**:

1. **Client-Side Blocking**:  
   - Replay UI disables command buttons  
   - Prevents accidental user submission

2. **Producer Validation** (GEM):  
   - Check `isReplay` flag on command envelope  
   - If `isReplay=true`, reject with error: "Commands cannot execute hardware during replay"

3. **Archive Tagging**:  
   - Commands table has `isReplay` column (default: false)  
   - Replay sessions tag all commands with `isReplay=true`  
   - Query: `SELECT * FROM commands WHERE isReplay=false` (excludes replay commands)

**Alternative Approach** *:  
Session-scoped transport subjects (not currently implemented):  
- Live session: `command.live.{verb}.{entityId}`
- Replay session: `command.replay.{sessionId}.{verb}.{entityId}`  
- GEM only subscribes to `command.live.*`

---

### Manifest-Driven Commands

**Manifest**: `gem.manifest.json`

**Structure**:
```json
{
  "actions": [
    {
      "verb": "receiver.hotStart",
      "actionId": "hotStart",
      "displayName": "Hot Start",
      "description": "Perform hot start (use existing almanac)",
      "targetType": "gnss-receiver",
      "params": []
    },
    {
      "verb": "receiver.coldStart",
      "actionId": "coldStart",
      "displayName": "Cold Start",
      "description": "Perform cold start (clear all ephemeris)",
      "targetType": "gnss-receiver",
      "params": []
    },
    {
      "verb": "receiver.uploadConfig",
      "actionId": "uploadConfig",
      "displayName": "Upload Configuration",
      "description": "Upload configuration file to receiver",
      "targetType": "gnss-receiver",
      "params": [
        {"name": "fileName", "type": "string", "required": true},
        {"name": "configData", "type": "string", "required": true}
      ]
    }
  ]
}
```

**Implementation**:
```python
class CommandHandler:
    def __init__(self, transport_manager, hardware_client, asset_repo, manifest):
        self.transport = transport_manager
        self.hardware = hardware_client
        self.asset_repo = asset_repo
        self.manifest = manifest
    
    async def start(self):
        # Subscribe to all command verbs in manifest
        for action in self.manifest['actions']:
            verb = action['verb']
            await self.transport.subscribe(f"command.{verb}.*", self.handle_command)
    
    async def handle_command(self, msg):
        subject = msg.subject  # command.receiver.hotStart.8220-F9P
        parts = subject.split('.')
        verb = '.'.join(parts[1:-1])  # receiver.hotStart
        entity_id = parts[-1]  # 8220-F9P
        
        # Parse command envelope
        cmd = json.loads(msg.data.decode())
        command_id = cmd['commandId']
        action_id = cmd['actionId']
        params = cmd.get('params', {})
        scope_id = cmd['scopeId']
        is_replay = cmd.get('isReplay', False)
        
        # REPLAY SAFETY: Block commands during replay
        if is_replay:
            await self.publish_result(command_id, entity_id, verb, "error", 
                                     "Commands cannot execute hardware during replay")
            return
        
        # Validate scope
        device = self.asset_repo.get_device(entity_id)
        if not device:
            await self.publish_result(command_id, entity_id, verb, "error", "Device not found")
            return
        
        if device.scope_id != scope_id:
            await self.publish_result(command_id, entity_id, verb, "error", 
                                     f"Scope mismatch: expected {device.scope_id}, got {scope_id}")
            return
        
        # Find action in manifest
        action = next((a for a in self.manifest['actions'] if a['actionId'] == action_id), None)
        if not action:
            await self.publish_result(command_id, entity_id, verb, "error", 
                                     f"Unknown action: {action_id}")
            return
        
        # Execute action
        try:
            result = await device.execute_action(action, params)
            await self.publish_result(command_id, entity_id, verb, "success", result['message'])
        except Exception as e:
            logger.exception(f"Command execution failed: {e}")
            await self.publish_result(command_id, entity_id, verb, "error", str(e))
    
    async def publish_result(self, command_id, entity_id, verb, status, message):
        result = {
            "commandId": command_id,
            "entityId": entity_id,
            "verb": verb,
            "status": status,
            "message": message,
            "timestampMs": int(time.time() * 1000),
            "source": "gem"
        }
        
        device = self.asset_repo.get_device(entity_id)
        scope_id = device.scope_id if device else "unknown"
        
        await self.transport.publish(
            f"archive.ingest.{scope_id}.command.result",
            json.dumps(result, sort_keys=True).encode()
        )
```

### Device Action Execution

**Implementation** (gnssReceiver.py):
```python
class GnssReceiver:
    async def execute_action(self, action, params):
        action_id = action['actionId']
        
        if action_id == 'hotStart':
            return await self._execute_hot_start()
        elif action_id == 'coldStart':
            return await self._execute_cold_start()
        elif action_id == 'uploadConfig':
            return await self._execute_upload_config(params)
        else:
            raise ValueError(f"Unknown action: {action_id}")
    
    async def _execute_hot_start(self):
        # Build UBX-CFG-RST (hot start)
        # Class: 0x06, ID: 0x04
        # Payload: navBbrMask=0x0000 (hot start), resetMode=0x09 (controlled GNSS warm start)
        payload = struct.pack('<HBB', 0x0000, 0x09, 0x00)
        ubx_msg = self._build_ubx_message(0x06, 0x04, payload)
        
        # Send via hardwareService IPC
        result = await self.hardware_client.apply_config(
            device_id=self.asset_id,
            config_bytes=ubx_msg,
            label="Hot Start"
        )
        
        if result['status'] == 'applied':
            return {"message": "Hot start executed"}
        else:
            raise Exception(result.get('message', 'Command failed'))
    
    async def _execute_upload_config(self, params):
        file_name = params['fileName']
        config_data_hex = params['configData']
        
        # Decode hex string to bytes
        config_bytes = bytes.fromhex(config_data_hex)
        
        # Send via hardwareService IPC
        result = await self.hardware_client.apply_config(
            device_id=self.asset_id,
            config_bytes=config_bytes,
            label=f"Config Upload: {file_name}"
        )
        
        if result['status'] == 'applied':
            return {"message": f"Configuration '{file_name}' uploaded successfully"}
        else:
            raise Exception(result.get('message', 'Upload failed'))
```

---

## Configuration Management

### Config File Structure

**GEM config.json**:
```json
{
  "scopeId": "payload-1",
  "systemId": "mission-alpha",
  "systemDisplayName": "Mission Alpha",
  "containerId": "payload-1",
  "containerDisplayName": "Payload 1",
  "nats": {
    "servers": ["nats://localhost:4222"],
    "maxReconnectAttempts": 10,
    "reconnectTimeWait": 2
  },
  "hardwareService": {
    "controlSubject": "hardwareService.control.Payload",
    "eventsSubject": "hardwareService.events.Payload"
  },
  "rateLimits": {
    "uiLaneHz": 2,
    "truthLaneHz": null
  }
}
```

### Configuration Apply (via IPC)

**Flow**:
1. Browser uploads config file (multipart form)
2. novaCore receives file, encodes as hex
3. novaCore publishes command with `configData` (hex string)
4. GEM receives command, decodes hex to bytes
5. GEM calls `hardwareClient.apply_config(device_id, config_bytes, label)`
6. hardwareClient publishes to `hardwareService.control.{containerId}` (REQ/REP)
7. hardwareService writes bytes to device serial port
8. hardwareService publishes result (applied, error, timeout)
9. GEM receives result, publishes to archive

**hardwareService REQ/REP**:
```python
class HardwareServiceClient:
    async def apply_config(self, device_id, config_bytes, label):
        request = {
            "command": "applyConfig",
            "deviceId": device_id,
            "configBytes": list(config_bytes),  # Convert to array
            "label": label
        }
        
        # Publish request with reply inbox
        response = await self.nats.request(
            "hardwareService.control.Payload",
            json.dumps(request).encode(),
            timeout=5.0
        )
        
        return json.loads(response.data.decode())
```

---

## Multi-Lane Publishing

### Three-Lane Architecture

**Overview**: GEM publishes the same data to three lanes with different characteristics.

**Lanes**:
1. **Raw Lane**: Binary passthrough for TCP replay (native rate)
2. **Truth Lane**: High-fidelity JSON messages (native rate, 10 Hz)
3. **UI Lane**: Rate-limited JSON messages (1-2 Hz)

### Rate Limiting (UI Lane)

**Implementation**:
```python
class RateLimiter:
    def __init__(self, rate_hz):
        self.rate_hz = rate_hz
        self.interval = 1.0 / rate_hz  # seconds
        self.last_publish = {}  # asset_id -> timestamp
    
    def should_publish(self, asset_id):
        now = time.time()
        last = self.last_publish.get(asset_id, 0)
        
        if now - last >= self.interval:
            self.last_publish[asset_id] = now
            return True
        return False
```

**Usage**:
```python
async def publish_ui_message(self, device, parsed_msg):
    # Rate limit by asset_id + streamType
    key = f"{device.asset_id}:{parsed_msg['messageType']}"
    
    if not self.ui_rate_limiter.should_publish(key):
        return  # Skip this message
    
    # Build stream envelope
    envelope = {
        "assetId": device.asset_id,
        "scopeId": device.scope_id,
        "streamType": parsed_msg['messageType'],
        "sequenceNum": device.get_next_sequence_num(parsed_msg['messageType']),
        "timestampMs": int(time.time() * 1000),
        "patch": parsed_msg['data'],
        "version": 1
    }
    
    # Publish to UI lane
    await self.nats.publish(
        f"stream.ui.{parsed_msg['messageType']}.{device.scope_id}.{device.asset_id}",
        json.dumps(envelope, sort_keys=True).encode()
    )
```

### Sequence Numbers

**Per-Entity, Per-StreamType**: Each entity maintains separate sequence counters for each streamType.

**Implementation**:
```python
class GnssReceiver:
    def __init__(self, asset_id, scope_id, container_id, parser):
        self.sequence_counters = {}  # streamType -> int
    
    def get_next_sequence_num(self, stream_type):
        if stream_type not in self.sequence_counters:
            self.sequence_counters[stream_type] = 0
        
        seq = self.sequence_counters[stream_type]
        self.sequence_counters[stream_type] += 1
        return seq
```

**Usage**: Detect message loss or reordering in archive or analysis tools.

---

## Error Handling & Recovery

### NATS Connection Failures

**Retry Logic**:
```python
async def connect_nats(self):
    while True:
        try:
            await self.nats.connect(
                servers=self.config['nats']['servers'],
                max_reconnect_attempts=self.config['nats']['maxReconnectAttempts'],
                reconnect_time_wait=self.config['nats']['reconnectTimeWait']
            )
            logger.info("Connected to NATS")
            return
        except Exception as e:
            logger.error(f"NATS connection failed: {e}")
            await asyncio.sleep(5)
```

**Reconnection Behavior**: NATS client auto-reconnects. GEM re-subscribes to all subjects on reconnect.

### Parser Errors

**Graceful Degradation**:
```python
async def handle_raw_bytes(self, msg):
    try:
        # Parse bytes
        for parsed_msg in device.parser.parse_bytes(msg.data):
            await self.publish_messages(device, parsed_msg)
    except Exception as e:
        logger.exception(f"Parser error for device {device.asset_id}: {e}")
        # Continue processing (don't crash service)
```

**Checksum Failures**: Log warning, advance buffer by 1 byte, continue parsing.

### Hardware Service Disconnection

**Detection**: Subscribe to `hardwareService.events.{containerId}`, monitor for disconnect events.

**Recovery**:
```python
async def handle_hardware_event(self, msg):
    event = json.loads(msg.data.decode())
    
    if event['event'] == 'disconnected':
        device_id = event['deviceId']
        device = self.asset_repo.get_device(device_id)
        if device:
            device.online = False
            await self.publish_metadata_update(device, {"online": False})
    
    elif event['event'] == 'topology':
        # Re-discover devices
        await self.handle_topology(event)
```

### Command Timeout

**Timeout Handling**:
```python
async def handle_command(self, msg):
    cmd = json.loads(msg.data.decode())
    
    try:
        # Execute with timeout
        result = await asyncio.wait_for(
            device.execute_action(action, params),
            timeout=10.0
        )
        await self.publish_result(cmd['commandId'], entity_id, verb, "success", result['message'])
    except asyncio.TimeoutError:
        await self.publish_result(cmd['commandId'], entity_id, verb, "timeout", 
                                 "Command timed out after 10 seconds")
    except Exception as e:
        await self.publish_result(cmd['commandId'], entity_id, verb, "error", str(e))
```

---

## Testing & Validation

### Unit Tests

**Parser Tests** (`test/test_parsers.py`):
```python
def test_ubx_nav_pvt_parsing():
    parser = UbxParser()
    
    # UBX-NAV-PVT message (92 bytes)
    ubx_msg = bytes.fromhex("B5 62 01 07 5C 00 ...")
    
    messages = list(parser.parse_bytes(ubx_msg))
    assert len(messages) == 1
    
    msg = messages[0]
    assert msg['messageType'] == 'position'
    assert abs(msg['data']['lat'] - 40.647002) < 1e-6
    assert abs(msg['data']['lon'] - -111.818352) < 1e-6
```

**Command Tests** (`test/testPhase4Commands.py`):
```python
async def test_hot_start_command():
    # Mock NATS and hardwareService
    nats_mock = MockNatsManager()
    hardware_mock = MockHardwareClient()
    
    # Create device
    device = GnssReceiver("8220-F9P", "payload-1", "payload-1", UbxParser())
    
    # Execute hot start
    result = await device.execute_action(
        {"actionId": "hotStart"},
        {}
    )
    
    assert result['message'] == "Hot start executed"
    assert hardware_mock.last_config_bytes is not None
```

### Integration Tests

**Device Integration** (`test/test_device_integration.py`):
```python
async def test_full_device_lifecycle():
    # Start GEM service
    gem = GemService(config)
    await gem.start()
    
    # Simulate topology event
    topology = {
        "event": "topology",
        "containerId": "Payload",
        "devices": [
            {"deviceId": "8220-F9P", "kind": "ubx", "subject": "device.raw.8220-F9P.ubx.serial"}
        ]
    }
    await gem.handle_topology(topology)
    
    # Simulate raw bytes
    ubx_pvt = bytes.fromhex("B5 62 01 07 5C 00 ...")
    await gem.handle_raw_bytes(MockMsg("device.raw.8220-F9P.ubx.serial", ubx_pvt))
    
    # Verify metadata published
    assert gem.asset_repo.get_device("8220-F9P").online == True
    
    # Verify message published (check NATS mock)
    assert nats_mock.published_count("stream.truth.position.payload-1.8220-F9P") == 1
    assert nats_mock.published_count("stream.ui.position.payload-1.8220-F9P") == 1
```

### IPC Tests

**hardwareService REQ/REP** (`test/test_ipc.py`):
```python
async def test_hardware_service_req_rep():
    # Start mock hardwareService
    hardware = MockHardwareService()
    await hardware.start()
    
    # Create GEM hardware client
    client = HardwareServiceClient(nats_manager)
    
    # Request topology
    topology = await client.get_topology()
    assert topology['event'] == 'topology'
    assert len(topology['devices']) > 0
    
    # Apply config
    config_bytes = bytes.fromhex("B5 62 06 04 04 00 00 00 09 00")
    result = await client.apply_config("8220-F9P", config_bytes, "Hot Start")
    assert result['status'] == 'applied'
```

---

## Summary

This document covers:

✅ **System Architecture**: Service boundaries, file structure, data flows  
✅ **Hardware Integration**: Topology discovery, raw stream processing, device lifecycle  
✅ **Message Parsing**: UBX, SBF, NMEA parsers with incremental parsing  
✅ **Metadata Management**: Change-only publishing, metadata structure, priority rules  
✅ **Command Execution**: Manifest-driven commands, IPC communication, result publishing  
✅ **Configuration Management**: Config apply flow, hardwareService REQ/REP  
✅ **Multi-Lane Publishing**: Raw/truth/UI lanes, rate limiting, sequence numbers  
✅ **Error Handling & Recovery**: NATS reconnection, parser errors, command timeouts  
✅ **Testing & Validation**: Unit tests, integration tests, IPC tests  

**Related Documents**:
- [nova architecture.md](nova%20architecture.md) - Full system architecture
- [nova api.md](nova%20api.md) - HTTP/WebSocket/NATS API reference

---

**Document Authority**: This is the master GEM implementation reference. All GEM code must follow these patterns.
