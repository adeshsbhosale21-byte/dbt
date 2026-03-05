import asyncio
import sys

sys.path.append(r"c:\Users\bhosa\Desktop\Langchain-V1-Crash-Course-main\nextin-rag-main\dbt_mcp_agent\backend")
from agent import app as agent_app
from langchain_core.messages import HumanMessage

async def main():
    state = {"messages": [("user", "Write a sql query to calculate total revenue in fct_sales")]}
    
    config = {"configurable": {"thread_id": "test_123"}}
    
    print("Testing LangGraph execution run...")
    async for output in agent_app.astream(state, config):
        for node_name, state_output in output.items():
            if node_name == "__metadata__": continue
            if "messages" in state_output and state_output["messages"]:
                latest_msg = state_output["messages"][-1]
                
                print(f"[{node_name}] {type(latest_msg)}")
                if hasattr(latest_msg, "tool_calls") and latest_msg.tool_calls:
                    print(f"-> TOOL CALL DETECTED: {latest_msg.tool_calls}")
                else:
                    print(f"-> Content: {latest_msg.content}")
                    
        if "__interrupt__" in output:
            print("Interrupt triggered as expected before tool execution!")

if __name__ == "__main__":
    asyncio.run(main())
