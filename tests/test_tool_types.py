import asyncio
import os
from langchain_openai import ChatOpenAI
from langchain_core.tools import StructuredTool, tool
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

async def _invoke_mcp_tool(**kwargs) -> str:
    return "Result"

class MyArgs(BaseModel):
    model_name: str = Field(..., description="The name of the model")

# Dynamic Tool
dynamic_tool = StructuredTool.from_function(
    coroutine=_invoke_mcp_tool,
    name="get_model_details",
    description="Gets details about a logical database model.",
    args_schema=MyArgs
)

@tool
def static_tool(model_name: str) -> str:
    """Gets details about a logical database model."""
    return "Result"

async def main():
    print("Testing Dynamic Tool Schema:")
    print(dynamic_tool.args_schema.schema())
    
    print("\nTesting Static Tool Schema:")
    print(static_tool.args_schema.schema())

    print("\n--- Testing dynamic tool with LLM ---")
    llm_with_dynamic = llm.bind_tools([dynamic_tool])
    res1 = await llm_with_dynamic.ainvoke([HumanMessage("Get details for fct_sales")])
    print("Dynamic tool_calls:", getattr(res1, "tool_calls", None))
    print("Content:", res1.content)

    print("\n--- Testing static tool with LLM ---")
    llm_with_static = llm.bind_tools([static_tool])
    res2 = await llm_with_static.ainvoke([HumanMessage("Get details for fct_sales")])
    print("Static tool_calls:", getattr(res2, "tool_calls", None))
    
if __name__ == "__main__":
    asyncio.run(main())
