#!/usr/bin/env python3
"""Check UI lane event structure"""
import asyncio
import websockets
import json

async def test():
    async with websockets.connect('ws://localhost:8080/ws') as ws:
        # Auth
        await ws.send(json.dumps({'type': 'auth', 'token': None}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if msg.get('success'):
            print('AUTH OK')
            
            # Start stream
            await ws.send(json.dumps({
                'type': 'startStream',
                'startTime': None,
                'stopTime': None,
                'rate': 1.0,
                'timelineMode': 'live',
                'timebase': 'canonical'
            }))
            
            # Wait for streamStarted
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            print(f'Stream: {msg}')
            
            # Receive chunks
            ui_events = []
            for _ in range(5):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    chunk = json.loads(raw)
                    if 'events' in chunk:
                        for ev in chunk['events']:
                            if ev.get('lane') == 'ui':
                                ui_events.append(ev)
                except asyncio.TimeoutError:
                    break
            
            print(f'\nUI Events received: {len(ui_events)}')
            if ui_events:
                sample = ui_events[0]
                print('Sample UI event:')
                print(f'  messageType: {sample.get("messageType")}')
                print(f'  viewId: {sample.get("viewId")}')
                print(f'  manifestId: {sample.get("manifestId")}')
                print(f'  systemId: {sample.get("systemId")}')
                print(f'  containerId: {sample.get("containerId")}')
                print(f'  uniqueId: {sample.get("uniqueId")}')
                print(f'  data: {sample.get("data")}')
        else:
            print(f'AUTH FAILED: {msg}')

asyncio.run(test())
