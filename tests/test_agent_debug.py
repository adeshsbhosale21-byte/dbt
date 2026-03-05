import asyncio
import os
import sys

# add parent dir so imports work like the actual app
sys.path.append(r"c:\Users\bhosa\Desktop\Langchain-V1-Crash-Course-main\nextin-rag-main\dbt_mcp_agent\backend")

from agent import run_agent_node, AgentState
from langchain_core.messages import HumanMessage

async def test_agent():
    state = {"messages": [HumanMessage(content="Hello, give me a status update on the data warehouse models.")]}
    try:
        print("Invoking run_agent_node...")
        result = await run_agent_node(state)
        print("Result:", result)
    except Exception as e:
        print(f"Error invoked: {e}")

if __name__ == "__main__":
    asyncio.run(test_agent())
