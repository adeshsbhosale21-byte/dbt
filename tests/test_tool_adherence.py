import asyncio
import websockets
import json

async def test_tool_adherence():
    uri = "ws://localhost:8000/ws/chat"
    try:
        async with websockets.connect(uri) as websocket:
            # 1. Send query that requires the 'show' tool
            payload = {"type": "chat", "content": "Show me the first 5 rows of fct_sales"}
            await websocket.send(json.dumps(payload))
            
            print("Sent: Show me the first 5 rows of fct_sales")
            
            # 2. Listen for approval_request
            hit_approval = False
            try:
                while True:
                    response = await asyncio.wait_for(websocket.recv(), timeout=20)
                    data = json.loads(response)
                    print(f"Received type: {data.get('type')}")
                    
                    if data.get("type") == "approval_request":
                        print("SUCCESS: Agent triggered an approval request for a tool!")
                        hit_approval = True
                        break
                    
                    if data.get("type") == "error":
                        print(f"ERROR: {data.get('content')}")
                        break
                        
                    if data.get("type") == "agent_state" and not hit_approval:
                        # If we get final state without approval first, it might have failed to use tool
                        content = data.get('content', '')
                        if "fct_sales" in content or "SELECT" in content:
                            print("WARNING: Agent talked about the data but did not trigger a tool call.")
                        else:
                            print(f"Agent state received: {content[:50]}...")
            except asyncio.TimeoutError:
                print("TIMEOUT: Agent took too long to respond.")
                
            if not hit_approval:
                print("FAIL: Agent did not trigger a tool call.")
    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    asyncio.run(test_tool_adherence())
