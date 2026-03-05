import asyncio
import websockets
import json

async def test_stop_signal():
    uri = "ws://localhost:8000/ws/chat"
    async with websockets.connect(uri) as websocket:
        print("Connected to websocket.")
        
        # 1. Send a complex question
        print("\nSending question: 'Show me total revenue by month'...")
        await websocket.send("Show me total revenue by month")
        
        # 2. Wait for it to start processing
        while True:
            response = await websocket.recv()
            data = json.loads(response)
            print(f"Server: {data}")
            if data.get("type") == "status" and data.get("content") == "busy":
                print("Agent is busy. Sending 'stop' signal in 1 second...")
                break
        
        await asyncio.sleep(1)
        
        # 3. Send "stop" while it's busy
        print("Sending: 'stop'...")
        await websocket.send("stop")
        
        # 4. Verify cancellation message
        while True:
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=10)
                data = json.loads(response)
                print(f"Server: {data}")
                if data.get("type") == "agent_state" and "cancelled" in data.get("content").lower():
                    print("SUCCESS: Execution cancelled!")
                    break
                if data.get("type") == "status" and data.get("content") == "ready":
                    print("SUCCESS: Status reset to ready!")
                    break
            except asyncio.TimeoutError:
                print("FAIL: No cancellation confirmation.")
                break

if __name__ == "__main__":
    asyncio.run(test_stop_signal())
