"""
Diagnose NOVA data flow end-to-end.
Checks: Database, streaming, entity discovery.
"""
import asyncio
import aiohttp
import sqlite3
from datetime import datetime, timezone, timedelta

async def diagnose():
    print("=" * 60)
    print("NOVA DATA FLOW DIAGNOSTIC")
    print("=" * 60)
    
    # 1. Check database directly
    print("\n1. DATABASE CHECK")
    print("-" * 40)
    try:
        db = sqlite3.connect('nova/nova_truth.db')
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Count events by lane
        for table in ['rawEvents', 'parsedEvents', 'uiEvents', 'commandEvents', 'metadataEvents']:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} events")
        
        # Check last 5 parsed events
        print("\n  Last 5 parsed events:")
        cursor.execute("""
            SELECT messageType, uniqueId, sourceTruthTime 
            FROM parsedEvents 
            ORDER BY rowid DESC 
            LIMIT 5
        """)
        for row in cursor.fetchall():
            print(f"    {row['messageType']} | {row['uniqueId']} | {row['sourceTruthTime']}")
        
        # Check for UI events
        print("\n  Last 5 UI events:")
        cursor.execute("""
            SELECT messageType, viewId, uniqueId, sourceTruthTime 
            FROM uiEvents 
            ORDER BY rowid DESC 
            LIMIT 5
        """)
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                print(f"    {row['messageType']} | {row['viewId']} | {row['uniqueId']} | {row['sourceTruthTime']}")
        else:
            print("    NO UI EVENTS IN DATABASE!")
        
        # Check for ubx.nav_pvt (our iTOW source)
        print("\n  Recent ubx.nav_pvt events:")
        cursor.execute("""
            SELECT COUNT(*) FROM parsedEvents 
            WHERE messageType = 'ubx.nav_pvt'
        """)
        count = cursor.fetchone()[0]
        print(f"    Total ubx.nav_pvt events: {count}")
        
        db.close()
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # 2. Check WebSocket streaming
    print("\n2. WEBSOCKET STREAMING CHECK")
    print("-" * 40)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect('ws://localhost:80/ws') as ws:
                # Auth
                await ws.send_json({'type': 'auth', 'token': None})
                resp = await ws.receive_json()
                print(f"  Auth: {resp.get('success')}")
                
                # Start LIVE stream
                await ws.send_json({
                    'type': 'startStream',
                    'startTime': None,
                    'stopTime': None,
                    'rate': 1.0,
                    'timelineMode': 'live',
                    'timebase': 'source',
                    'filters': None
                })
                resp = await ws.receive_json()
                print(f"  Stream started: {resp.get('playbackRequestId', 'N/A')[:8]}...")
                
                # Listen for 3 chunks
                lanes_seen = {}
                messageTypes_seen = {}
                for i in range(3):
                    try:
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
                        if msg.get('type') == 'streamChunk':
                            events = msg.get('events', [])
                            for e in events:
                                lane = e.get('lane', 'unknown')
                                lanes_seen[lane] = lanes_seen.get(lane, 0) + 1
                                mt = e.get('messageType')
                                if mt:
                                    messageTypes_seen[mt] = messageTypes_seen.get(mt, 0) + 1
                            print(f"  Chunk {i}: {len(events)} events")
                    except asyncio.TimeoutError:
                        print(f"  Chunk {i}: TIMEOUT")
                
                print(f"\n  Lanes seen: {lanes_seen}")
                print(f"  Message types (top 5): {dict(sorted(messageTypes_seen.items(), key=lambda x: -x[1])[:5])}")
                
                # Check for nav_pvt specifically
                nav_pvt_count = messageTypes_seen.get('ubx.nav_pvt', 0)
                print(f"\n  ubx.nav_pvt events streamed: {nav_pvt_count}")
                if nav_pvt_count == 0:
                    print("  WARNING: No ubx.nav_pvt events - iTOW display will not update!")
                
                # Check for UI lane
                ui_count = lanes_seen.get('ui', 0)
                print(f"  UI lane events: {ui_count}")
                if ui_count == 0:
                    print("  WARNING: No UI lane events - manifest cards will not render!")
                    
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # 3. Summary
    print("\n3. DIAGNOSIS SUMMARY")
    print("-" * 40)
    print("  Check the output above to identify:")
    print("  - If ubx.nav_pvt exists in DB but not streaming: streaming issue")
    print("  - If UI events missing from DB: UiUpdate not being published")
    print("  - If no ubx.nav_pvt at all: hardwareService not producing data")

if __name__ == '__main__':
    asyncio.run(diagnose())
