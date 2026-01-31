"""Quick WebSocket test for NOVA data flow"""
import asyncio
import aiohttp

async def test():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://localhost:80/ws') as ws:
            # Auth
            await ws.send_json({'type': 'auth', 'token': None})
            resp = await ws.receive_json()
            print(f'Auth: {resp}')
            
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
            print(f'Stream: {resp}')
            
            # Listen for events
            print('Listening for events...')
            for i in range(10):
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                    print(f'Event {i}: type={msg.get("type")} events={len(msg.get("events",[]))}')
                    if msg.get('events'):
                        for e in msg['events'][:3]:
                            print(f'  lane={e.get("lane")} uniqueId={e.get("uniqueId")} messageType={e.get("messageType")}')
                except asyncio.TimeoutError:
                    print(f'Timeout {i}')
                    
asyncio.run(test())
