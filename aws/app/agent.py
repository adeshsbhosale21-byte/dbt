import os
from typing import TypedDict, Annotated, Sequence
import operator
import asyncio
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_aws import ChatBedrock
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from app.mcp_client import mcp_integration
from dotenv import load_dotenv
import boto3
from app.logger import get_logger

logger = get_logger("agent")

# Load environment variables (Check root and parent dirs)
load_dotenv() # Check current dir
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env")) # Check app/../.env
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")) # Check legacy depth

# Define state dictionary for LangGraph
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]

# Instantiate LLM (Default to AWS Bedrock, Fallback to OpenAI)
def get_llm():
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    bedrock_model = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20240620-v1:0")
    
    try:
        # Standard AWS Credential Chain (will use IAM Roles if available)
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if credentials:
            logger.info(f"Initializing ChatBedrock with model {bedrock_model} in {aws_region}")
            return ChatBedrock(
                model_id=bedrock_model,
                region_name=aws_region
            ), False
        else:
            raise ValueError("No AWS credentials found (ENV, Config, or Role)")
            
    except Exception as e:
        logger.warning(f"AWS Bedrock initialization failed: {e}")
        if os.environ.get("OPENAI_API_KEY"):
            logger.info("Using OpenAI fallback.")
            return ChatOpenAI(model="gpt-4o-mini", temperature=0), False
        else:
            logger.error("No valid LLM credentials found. Using Mock mode.")
            return None, True

llm, USE_MOCK = get_llm()

async def run_agent_node(state: AgentState):
    """
    Main Agent Node. Dynamically fetches full-fledged MCP tools from the dbt-mcp server and executes.
    """
    logger.debug(f"Entering 'agent' node. Message count: {len(state['messages'])}")
    tools = await mcp_integration.get_langchain_tools()
    
    # Hallucination prevention: If no tools found, inform agent so it doesn't fake them
    if not tools:
        logger.warning("No tools discovered! Disabling tool-based capabilities for this turn.")
        system_prompt = (
            "You are a Data Analytics Agent, but you CURRENTLY HAVE NO ACCESS to the dbt project tools due to a connection or configuration issue.\n"
            "MANDATE: Inform the user that you currently don't have access to the models or tables and suggest they check the logs or DBT_PROJECT_DIR setting.\n"
            "DO NOT hallucinate tool names or output raw JSON blocks."
        )
        messages_for_llm = [SystemMessage(content=system_prompt)] + state["messages"]
        response = await llm.ainvoke(messages_for_llm)
        return {"messages": [response]}

    llm_with_tools = llm.bind_tools(tools)
    tool_names = ", ".join([t.name for t in tools])
    
    system_prompt = (
        f"You are a high-performance Data Analytics Agent. You have DIRECT access to a dbt project and data warehouse via these tools: {tool_names}.\n\n"
        "CORE MANDATE:\n"
        "1. NEVER say 'I don't have the capability to execute SQL' or 'Run this in your environment'. YOU CAN run it using the 'show' tool.\n"
        "2. If the user asks for data (counts, rows, sums), YOU MUST use the 'show' tool. Do not just print the SQL code.\n"
        "3. First, use 'list' to find models. Then use 'get_node_details_dev' to understand schema. Finally use 'show' for results.\n"
        "4. Your SQL for 'show' should be valid Jinja/SQL compatible with the target warehouse (usually DuckDB or Redshift).\n\n"
        "SECURITY & FORMATTING:\n"
        "- NO destructive commands (DROP, DELETE, etc.).\n"
        "- ONLY use native function calling (no raw JSON blocks in text).\n"
        "- Always be concise and data-driven."
    )
    
    logger.debug(f"System Prompt length: {len(system_prompt)}")
    messages_for_llm = [SystemMessage(content=system_prompt)] + state["messages"]
    
    if USE_MOCK:
        logger.info("Using MOCK execution for agent node")
        response = AIMessage(content="Simulated full-fledged dbt-mcp execution against Redshift completed.")
    else:
        logger.info("Invoking LLM for agent node...")
        try:
            response = await asyncio.wait_for(
                llm_with_tools.ainvoke(messages_for_llm),
                timeout=60.0
            )
            logger.debug(f"LLM Response received: tool_calls={getattr(response, 'tool_calls', [])}")
        except asyncio.TimeoutError:
            logger.error("LLM call timed out after 60 seconds!")
            response = AIMessage(content="Sorry, the LLM call timed out. Please try again.")
        
    return {"messages": [response]}


async def handle_tool_execution(state: AgentState):
    """
    Routes active bedrocks LLM tool calls directly to the dbt-mcp subprocess.
    """
    last_message = state["messages"][-1]
    responses = []
    
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        t_calls = last_message.tool_calls
        logger.info(f"Entering 'tools' node. Executing {len(t_calls)} tools: {[call['name'] for call in t_calls]}")
        tools = await mcp_integration.get_langchain_tools()
        tools_map = {t.name: t for t in tools}
        
        for call in t_calls:
            t_name = call["name"]
            logger.debug(f"Invoking tool '{t_name}' with args: {call['args']}")
            tool_fn = tools_map.get(t_name)
            if tool_fn:
                try:
                    result = await tool_fn.ainvoke(call["args"])
                    logger.debug(f"Tool '{t_name}' success. Result length: {len(str(result))}")
                    responses.append(ToolMessage(
                        content=result,
                        tool_call_id=call["id"],
                        name=t_name
                    ))
                except Exception as e:
                    logger.error(f"Error executing tool '{t_name}': {e}", exc_info=True)
                    responses.append(ToolMessage(
                        content=f"Error: {str(e)}",
                        tool_call_id=call["id"],
                        name=t_name
                    ))
            else:
                logger.error(f"Tool '{t_name}' not found in registered tools!")
                responses.append(ToolMessage(
                    content=f"Error: Tool '{t_name}' not found.",
                    tool_call_id=call["id"],
                    name=t_name
                ))
                
    return {"messages": responses}

def should_continue(state: AgentState):
    """Router conditional logic for LangGraph."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END

from langgraph.checkpoint.memory import MemorySaver

# Build Graph Structure with Human-in-the-Loop Memory
memory = MemorySaver()
workflow = StateGraph(AgentState)

workflow.add_node("agent", run_agent_node)
workflow.add_node("tools", handle_tool_execution)

workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

# Compile with interrupt before tools
app = workflow.compile(checkpointer=memory, interrupt_before=["tools"])
