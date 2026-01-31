"""Test replay data flow - queries past data from database"""
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

async def test_replay():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://localhost:80/ws') as ws:
            # Auth
            await ws.send_json({'type': 'auth', 'token': None})
            resp = await ws.receive_json()
            print(f'Auth: {resp}')
            
            # Request REWIND stream (past 5 minutes)
            now = datetime.now(timezone.utc)
            startTime = now - timedelta(minutes=5)
            stopTime = now - timedelta(minutes=1)
            
            await ws.send_json({
                'type': 'startStream',
                'startTime': startTime.isoformat(),
                'stopTime': stopTime.isoformat(),
                'rate': 10.0,  # 10x speed
                'timelineMode': 'replay',
                'timebase': 'source',
                'filters': None
            })
            resp = await ws.receive_json()
            print(f'Stream: {resp}')
            
            # Listen for events
            print('Listening for replay events...')
            totalEvents = 0
            lanes = {}
            for i in range(20):
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                    msgType = msg.get('type')
                    
                    if msgType == 'streamChunk':
                        events = msg.get('events', [])
                        totalEvents += len(events)
                        for e in events:
                            lane = e.get('lane', 'unknown')
                            lanes[lane] = lanes.get(lane, 0) + 1
                        print(f'Chunk {i}: {len(events)} events (total: {totalEvents})')
                    elif msgType == 'streamComplete':
                        print(f'Stream complete! Total: {totalEvents} events')
                        print(f'By lane: {lanes}')
                        break
                    else:
                        print(f'Other: {msgType}')
                        
                except asyncio.TimeoutError:
                    print(f'Timeout {i}')
                    
            print(f'\nFinal: {totalEvents} events across lanes: {lanes}')
                    
asyncio.run(test_replay())
