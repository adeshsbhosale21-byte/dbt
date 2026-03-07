import asyncio
import os
import sys

# Force local backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(override=True)
os.environ["AZURE_PG_CONNECTION_STRING"] = ""

from app.agent import llm
from app.mcp_client import mcp_integration

async def debug():
    tools = await mcp_integration.get_langchain_tools()
    print("Tools:")
    for t in tools:
        print(f"- {t.name}: {t.description}")
        print(f"  Schema: {t.args_schema.schema() if t.args_schema else 'None'}")
    
    print("\nBinding tools to LLM...")
    try:
        llm_with_tools = llm.bind_tools(tools)
        print("Successfully bound tools.")
    except Exception as e:
        print(f"Error binding tools: {e}")

if __name__ == "__main__":
    asyncio.run(debug())
