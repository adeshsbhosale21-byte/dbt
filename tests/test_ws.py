import asyncio
import websockets

async def test_ws():
    uri = "ws://localhost:8000/ws/chat"
    try:
        async with websockets.connect(uri) as websocket:
            print("Successfully connected to websocket!")
            await websocket.send("Write a SQL query to calculate total revenue from the fct_sales model and execute it for me.")
            
            # Wait for multiple responses (agent state or approval request)
            for _ in range(5):
                response = await websocket.recv()
                print(f"Received: {response}")
                if "approval_request" in response:
                    break
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
