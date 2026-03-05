import asyncio
import sys
sys.path.append(r"c:\Users\bhosa\Desktop\Langchain-V1-Crash-Course-main\nextin-rag-main\dbt_mcp_agent\backend")
from mcp_client import mcp_integration

async def test():
    tools = await mcp_integration.get_langchain_tools()
    for t in tools:
        print(f"Tool: {t.name}")
        if t.args_schema:
            print(t.args_schema.model_json_schema())
        else:
            print("No args schema")
        print("---")

asyncio.run(test())
