import asyncio
import os
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

@tool
def get_model_details(model_name: str) -> str:
    """Gets details about a logical database model."""
    return f"Details for {model_name}"

async def main():
    llm_with_tools = llm.bind_tools([get_model_details])
    
    messages = [
        SystemMessage("You are an AI assistant. You must use the tools provided."),
        HumanMessage("Get details for fct_sales")
    ]
    
    response = await llm_with_tools.ainvoke(messages)
    
    print("Response Content:", response.content)
    print("Tool Calls:", getattr(response, "tool_calls", None))

if __name__ == "__main__":
    asyncio.run(main())
