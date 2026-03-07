import os
from typing import TypedDict, Annotated, Sequence
import operator
import asyncio
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from app.mcp_client import mcp_integration
from dotenv import load_dotenv
from app.logger import get_logger

logger = get_logger("agent")

# Load environment variables
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Define state dictionary for LangGraph
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]

# ─── LLM Initialization ───
# Priority: Azure AI Foundry > Azure OpenAI > Direct OpenAI > Mock
def get_llm():
    # === 1. Azure AI Foundry (Recommended - supports GPT, Llama, Phi, Cohere, etc.) ===
    foundry_endpoint = os.environ.get("AZURE_AI_FOUNDRY_ENDPOINT")
    foundry_credential = os.environ.get("AZURE_AI_FOUNDRY_KEY")
    foundry_model = os.environ.get("AZURE_AI_FOUNDRY_MODEL", "gpt-4o")

    if foundry_endpoint and foundry_credential:
        try:
            from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel
            logger.info(f"Initializing Azure AI Foundry: model={foundry_model}, endpoint={foundry_endpoint}")
            return AzureAIChatCompletionsModel(
                endpoint=foundry_endpoint,
                credential=foundry_credential,
                model_name=foundry_model,
                temperature=0
            ), False
        except ImportError:
            logger.warning("langchain-azure-ai not installed. Trying Azure OpenAI fallback.")
        except Exception as e:
            logger.warning(f"Azure AI Foundry init failed: {e}")

    # === 2. Azure OpenAI (Classic deployment) ===
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY")
    azure_deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    azure_api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

    if azure_endpoint and azure_key:
        try:
            from langchain_openai import AzureChatOpenAI
            logger.info(f"Initializing Azure OpenAI: deployment={azure_deployment}")
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                azure_deployment=azure_deployment,
                api_key=azure_key,
                api_version=azure_api_version,
                temperature=0
            ), False
        except Exception as e:
            logger.warning(f"Azure OpenAI init failed: {e}")

    # === 3. Direct OpenAI (Fallback) ===
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        logger.info("Using direct OpenAI fallback.")
        return ChatOpenAI(model="gpt-4o-mini", temperature=0), False

    # === 4. Mock ===
    logger.error("No LLM credentials found. Using Mock mode.")
    return None, True

llm, USE_MOCK = get_llm()


async def run_agent_node(state: AgentState):
    """
    Main Agent Node. Dynamically fetches MCP tools from the dbt-mcp server.
    """
    logger.debug(f"Entering 'agent' node. Message count: {len(state['messages'])}")
    tools = await mcp_integration.get_langchain_tools()
    
    if not tools:
        logger.warning("No tools discovered!")
        system_prompt = (
            "You are a Data Analytics Agent, but you CURRENTLY HAVE NO ACCESS to the dbt project tools.\n"
            "MANDATE: Inform the user that you currently don't have access and suggest checking the logs or DBT_PROJECT_DIR.\n"
            "DO NOT hallucinate tool names or output raw JSON blocks."
        )
        messages_for_llm = [SystemMessage(content=system_prompt)] + state["messages"]
        response = await llm.ainvoke(messages_for_llm)
        return {"messages": [response]}

    llm_with_tools = llm.bind_tools(tools)
    tool_names = ", ".join([t.name for t in tools])
    
    system_prompt = (
        f"You are a high-performance Data Analytics Agent powered by Azure AI. "
        f"You have DIRECT access to a dbt project and data warehouse via these tools: {tool_names}.\n\n"
        "CORE MANDATE:\n"
        "1. NEVER say 'I don't have the capability to execute SQL'. YOU CAN run it using the 'show' tool.\n"
        "2. If the user asks for data (counts, rows, sums), YOU MUST use the 'show' tool.\n"
        "3. First, use 'list' to find models. Then 'get_node_details_dev' for schema. Finally 'show' for results.\n"
        "4. Your SQL should be valid Jinja/SQL for the target warehouse (DuckDB or Azure Synapse).\n\n"
        "SECURITY & FORMATTING:\n"
        "- NO destructive commands (DROP, DELETE, etc.).\n"
        "- ONLY use native function calling (no raw JSON blocks in text).\n"
        "- Always be concise and data-driven."
    )
    
    logger.debug(f"System Prompt length: {len(system_prompt)}")
    messages_for_llm = [SystemMessage(content=system_prompt)] + state["messages"]
    
    if USE_MOCK:
        logger.info("Using MOCK execution for agent node")
        response = AIMessage(content="Simulated dbt-mcp execution against Azure Synapse completed.")
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
    """Routes LLM tool calls to the dbt-mcp subprocess."""
    last_message = state["messages"][-1]
    responses = []
    
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        t_calls = last_message.tool_calls
        logger.info(f"Executing {len(t_calls)} tools: {[call['name'] for call in t_calls]}")
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
                    responses.append(ToolMessage(content=result, tool_call_id=call["id"], name=t_name))
                except Exception as e:
                    logger.error(f"Error executing tool '{t_name}': {e}", exc_info=True)
                    responses.append(ToolMessage(content=f"Error: {str(e)}", tool_call_id=call["id"], name=t_name))
            else:
                logger.error(f"Tool '{t_name}' not found!")
                responses.append(ToolMessage(content=f"Error: Tool '{t_name}' not found.", tool_call_id=call["id"], name=t_name))
                
    return {"messages": responses}

def should_continue(state: AgentState):
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        # HUMAN APPROVAL LOGIC:
        # Approval is ONLY required for database-querying tools (show, query).
        # Metadata tools like 'list' are considered safe.
        safe_tools = ["list"]
        for call in last_message.tool_calls:
            if call["name"] not in safe_tools:
                return "sensitive_tools"
        return "safe_tools"
    return END

from langgraph.checkpoint.memory import MemorySaver

def compile_agent(checkpointer=None):
    """Compiles the agent graph with an optional persistent checkpointer."""
    if checkpointer is None:
        checkpointer = MemorySaver()
        
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", run_agent_node)
    
    # We use the same handler for both nodes, but interrupt only before sensitive_tools
    workflow.add_node("safe_tools", handle_tool_execution)
    workflow.add_node("sensitive_tools", handle_tool_execution)
    
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent", 
        should_continue, 
        {"safe_tools": "safe_tools", "sensitive_tools": "sensitive_tools", END: END}
    )
    workflow.add_edge("safe_tools", "agent")
    workflow.add_edge("sensitive_tools", "agent")
    
    # The 'sensitive_tools' node will trigger the 'interrupt' requiring human approval in UI
    return workflow.compile(checkpointer=checkpointer, interrupt_before=["sensitive_tools"])

# Default in-memory app for fallback or simple runs
app = compile_agent(MemorySaver())
