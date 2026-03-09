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

# ─── Phoenix Tracing Setup ───
import phoenix as px
from phoenix.otel import register
from openinference.instrumentation.langchain import LangChainInstrumentor

logger.info("Initializing Arize Phoenix Tracing...")
try:
    # Set collector endpoint for OpenInference (internal OTLP)
    os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "http://127.0.0.1:6006"
    
    # Launch local Phoenix server. It will run in the background.
    session = px.launch_app(host="0.0.0.0", port=6006)
    
    # Explicitly register the tracer provider for production performance
    # This ensures spans are exported in the background with BatchSpanProcessor by default
    tracer_provider = register(
        project_name="dbt-mcp-agent"
    )
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
    
    logger.info(f"Phoenix Tracing active! Access dashboard at: {session.url}")
except Exception as e:
    logger.error(f"Failed to initialize Phoenix: {e}")

# Define state dictionary for LangGraph
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    plan: str

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


async def planner_node(state: AgentState):
    """Generates a high-level plan for the dbt task."""
    logger.info("Entering 'planner' node")
    
    # Simple strategy: If it's the first message, plan. Otherwise, skip or refine.
    if len(state["messages"]) > 1 and any(isinstance(m, ToolMessage) for m in state["messages"]):
        return {"plan": state.get("plan", "Follow the existing steps.")}

    prompt = (
        "You are a dbt Strategy Planner. Analyze the user request and define 2-3 steps to solve it.\n"
        "Available dbt tools: list (to find models), get_node_details (to see columns), show (to run query).\n"
        "MANDATE: Prioritize discovery (list/get_node_details) before querying (show).\n"
        "Do NOT hallucinate or output the actual data in your plan. The plan should only be instructions for the executor on which tools to call.\n"
        "Output ONLY your plan as a concise bulleted list."
    )
    
    messages = [SystemMessage(content=prompt)] + state["messages"]
    response = await llm.ainvoke(messages)
    
    return {
        "messages": [AIMessage(content=f"**Plan:**\n{response.content}")],
        "plan": response.content
    }

async def executor_node(state: AgentState):
    """
    Executor Node (formerly agent node). Executes tools to fulfill the plan.
    """
    logger.debug(f"Entering 'executor' node. Message count: {len(state['messages'])}")
    tools = await mcp_integration.get_langchain_tools()
    
    if not tools:
        logger.warning("No tools discovered!")
        return {"messages": [AIMessage(content="Error: No dbt tools available.")]}

    llm_with_tools = llm.bind_tools(tools)
    tool_names = ", ".join([t.name for t in tools])
    
    plan_context = state.get("plan", "No plan defined.")
    system_prompt = (
        f"You are a Data Analytics Executor. Your current goal is to follow this plan:\n{plan_context}\n\n"
        f"Available tools: {tool_names}.\n"
        "CRITICAL RULES:\n"
        "1. YOU MUST ACTUALLY CALL THE PROVIDED TOOLS TO FETCH DATA. DO NOT pretend to use them.\n"
        "2. NEVER output placeholders like '[Listing models...]' or '[The actual model names would be listed here]'. If you don't know the models, you MUST EXPLICITLY CALL the `list` tool.\n"
        "3. Use tools strictly one by one to fulfill the plan. Wait for the tool result before proceeding.\n"
        "4. If you have enough real data from the tools, provide the final answer.\n"
        "5. NEVER hallucinate data or model names. If you need to see rows, use 'show'."
    )
    
    messages_for_llm = [SystemMessage(content=system_prompt)] + state["messages"]
    
    if USE_MOCK:
        return {"messages": [AIMessage(content="[Mock] Executing plan step...")]}
    
    try:
        response = await asyncio.wait_for(
            llm_with_tools.ainvoke(messages_for_llm),
            timeout=60.0
        )
        return {"messages": [response]}
    except asyncio.TimeoutError:
        return {"messages": [AIMessage(content="Execution timed out.")]}


async def handle_tool_execution(state: AgentState):
    """Routes LLM tool calls to the dbt-mcp subprocess in parallel."""
    last_message = state["messages"][-1]
    
    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"messages": []}

    t_calls = last_message.tool_calls
    logger.info(f"Executing {len(t_calls)} tools in parallel: {[call['name'] for call in t_calls]}")
    
    tools = await mcp_integration.get_langchain_tools()
    tools_map = {t.name: t for t in tools}
    
    async def run_one_tool(call):
        t_name = call["name"]
        tool_fn = tools_map.get(t_name)
        if not tool_fn:
            return ToolMessage(content=f"Error: Tool '{t_name}' not found.", tool_call_id=call["id"], name=t_name)
        try:
            result = await tool_fn.ainvoke(call["args"])
            return ToolMessage(content=result, tool_call_id=call["id"], name=t_name)
        except Exception as e:
            return ToolMessage(content=f"Error: {str(e)}", tool_call_id=call["id"], name=t_name)

    # Execute all tool calls concurrently
    responses = await asyncio.gather(*(run_one_tool(call) for call in t_calls))
                
    return {"messages": list(responses)}

def should_continue(state: AgentState):
    """Determines if the graph should proceed to tools or end."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        # SAFE TOOLS: list, get_node_details, get_node_details_dev
        # NO human approval needed for these.
        safe_tools = ["list", "get_node_details", "get_node_details_dev"]
        for call in last_message.tool_calls:
            t_name = call["name"].lower().strip()
            if t_name not in safe_tools:
                return "sensitive_tools"
        return "safe_tools"
    return END

from langgraph.checkpoint.memory import MemorySaver

def compile_agent(checkpointer=None):
    """Compiles the planner-executor graph."""
    if checkpointer is None:
        checkpointer = MemorySaver()
        
    workflow = StateGraph(AgentState)
    
    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("safe_tools", handle_tool_execution)
    workflow.add_node("sensitive_tools", handle_tool_execution)
    
    workflow.set_entry_point("planner")
    
    workflow.add_edge("planner", "executor")
    
    workflow.add_conditional_edges(
        "executor", 
        should_continue, 
        {"safe_tools": "safe_tools", "sensitive_tools": "sensitive_tools", END: END}
    )
    
    workflow.add_edge("safe_tools", "executor")
    workflow.add_edge("sensitive_tools", "executor")
    
    return workflow.compile(checkpointer=checkpointer, interrupt_before=["sensitive_tools"])

# Global app instance
app = compile_agent(MemorySaver())
