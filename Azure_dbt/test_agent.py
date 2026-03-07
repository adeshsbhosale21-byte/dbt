import asyncio
import os
import sys

# Force local backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import dotenv
dotenv.load_dotenv(override=True)

from app.agent import compile_agent, get_logger
from langchain_core.messages import HumanMessage

async def debug_agent():
    app = compile_agent()
    config = {"configurable": {"thread_id": "debug_123"}}
    
    print("Sending message to agent...")
    async for output in app.astream({"messages": [HumanMessage(content="list the models")]}, config):
        for node_name, state_output in output.items():
            if node_name == "__metadata__": continue
            print(f"\n--- Node: {node_name} ---")
            if "messages" in state_output:
                for msg in state_output["messages"]:
                    print(f"[{msg.type}]: {msg.content}")
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        print(f"Tool Calls: {msg.tool_calls}")

if __name__ == "__main__":
    asyncio.run(debug_agent())
