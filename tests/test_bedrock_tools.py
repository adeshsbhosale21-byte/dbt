import sys
import asyncio
from langchain_aws import ChatBedrock
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool

sys.path.append(r"c:\Users\bhosa\Desktop\Langchain-V1-Crash-Course-main\nextin-rag-main\dbt_mcp_agent\backend")

llm = ChatBedrock(
    model_id="anthropic.claude-3-5-sonnet-20240620-v1:0", 
    region_name="us-east-1"
)

@tool
def dummy_tool(query: str) -> str:
    """A dummy tool to test tool calling."""
    return f"Result for {query}"

async def test():
    llm_with_tools = llm.bind_tools([dummy_tool])
    messages = [
        SystemMessage(content="You are a helpful assistant. Please use the dummy_tool to answer the question."),
        HumanMessage(content="Please test the dummy tool with the query 'hello'")
    ]
    response = await llm_with_tools.ainvoke(messages)
    print("Response Content:", response.content)
    print("Tool Calls:", getattr(response, "tool_calls", None))

if __name__ == "__main__":
    asyncio.run(test())
