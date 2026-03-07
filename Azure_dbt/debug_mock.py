import asyncio
import os
import sys

# use local mock to skip azure pg conn
os.environ["AZURE_PG_CONNECTION_STRING"] = ""

from app.agent import llm, compile_agent
from app.mcp_client import mcp_integration
from langchain_core.messages import HumanMessage, SystemMessage

async def debug_tool_binding():
    print(f"LLM initialized: {llm}")
    tools = await mcp_integration.get_langchain_tools()
    print(f"Tools Discovered: {[t.name for t in tools]}")
    
    if not tools:
        print("No tools found.")
        return

    llm_with_tools = llm.bind_tools(tools)
    print("Testing explicit list call probability...")
    
    messages = [
        SystemMessage(content="You are an executor. You must use the `list` tool immediately."),
        HumanMessage(content="list the models")
    ]
    
    response = await llm_with_tools.ainvoke(messages)
    print(f"LLM Output Type: {type(response)}")
    print(f"Content: {response.content}")
    if hasattr(response, "tool_calls"):
        print(f"Tool Calls: {response.tool_calls}")
    else:
        print("NO TOOL CALLS FOUND")

if __name__ == "__main__":
    asyncio.run(debug_tool_binding())
