"""
Redesigned MCP integration that:
1. Fetches the dbt-mcp tool schemas ONCE via a subprocess call (not async)
2. Calls dbt-mcp tools via subprocess JSON-RPC (not async anyio context)
This avoids the anyio vs asyncio event loop conflict inside Uvicorn.
"""

import os
import sys
import json
import subprocess
import asyncio
from typing import List, Any
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model
from app.logger import get_logger

logger = get_logger("mcp_client")


def _get_venv_scripts():
    scripts_dir = os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")
    return scripts_dir


def _get_dbt_project_dir():
    return os.environ.get("DBT_PROJECT_DIR", 
        os.path.join(os.path.dirname(__file__), "..", "local_dbt_test"))


def fetch_mcp_tool_schemas() -> list:
    """
    Fetches the list of available tools from dbt-mcp by running it as a subprocess
    and sending a ListTools JSON-RPC request over stdin/stdout.
    Returns a list of dicts: [{name, description, inputSchema}, ...]
    """
    scripts_dir = _get_venv_scripts()
    env = os.environ.copy()
    env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
    env["DBT_PATH"] = os.path.join(scripts_dir, "dbt.exe" if os.name == "nt" else "dbt")
    
    dbt_project_dir = os.path.abspath(_get_dbt_project_dir())
    # dbt-mcp requires DBT_PROJECT_DIR as an env var, not just a CLI flag
    env["DBT_PROJECT_DIR"] = dbt_project_dir
    env["DBT_PROFILES_DIR"] = dbt_project_dir
    
    # MCP init handshake + list tools request
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent", "version": "1.0"}
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    ]
    
    stdin_data = "\n".join(json.dumps(m) for m in messages) + "\n"
    
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "dbt_mcp.main", "--project-dir", dbt_project_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        stdout, stderr = proc.communicate(input=stdin_data, timeout=60)
        
        if stderr:
            logger.debug(f"dbt-mcp discovery stderr: {stderr}")

        tools = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict) and data.get("id") == 2:
                    raw_tools = data.get("result", {}).get("tools", [])
                    tools = raw_tools
                    break
            except json.JSONDecodeError:
                continue
        return tools
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.error("dbt-mcp schema fetch timed out after 60s")
        return []
    except Exception as e:
        logger.error(f"dbt-mcp schema fetch error: {e}")
        return []


def call_mcp_tool_sync(tool_name: str, arguments: dict) -> str:
    """
    Calls a dbt-mcp tool synchronously via subprocess JSON-RPC.
    """
    scripts_dir = _get_venv_scripts()
    env = os.environ.copy()
    env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
    env["DBT_PATH"] = os.path.join(scripts_dir, "dbt.exe" if os.name == "nt" else "dbt")
    dbt_project_dir = os.path.abspath(_get_dbt_project_dir())
    env["DBT_PROJECT_DIR"] = dbt_project_dir
    env["DBT_PROFILES_DIR"] = dbt_project_dir
    
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent", "version": "1.0"}
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": tool_name,
            "arguments": arguments
        }}
    ]
    
    stdin_data = "\n".join(json.dumps(m) for m in messages) + "\n"
    
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "dbt_mcp.main", "--project-dir", dbt_project_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        stdout, stderr = proc.communicate(input=stdin_data, timeout=60)
        
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict) and data.get("id") == 2:
                    content = data.get("result", {}).get("content", [])
                    if isinstance(content, list):
                        return " ".join(c.get("text", "") for c in content)
                    return str(content)
            except json.JSONDecodeError:
                continue
        return "No result returned."
    except subprocess.TimeoutExpired:
        proc.kill()
        return f"Tool '{tool_name}' timed out after 60s."
    except Exception as e:
        return f"Tool error: {e}"


# Cache tool schemas at module load time (once)
_cached_tool_schemas = None

def get_cached_tool_schemas() -> list:
    global _cached_tool_schemas
    if _cached_tool_schemas is None:
        logger.info("Fetching dbt-mcp tool schemas (one time)...")
        _cached_tool_schemas = fetch_mcp_tool_schemas()
        logger.info(f"Found {len(_cached_tool_schemas)} dbt-mcp tools")
    return _cached_tool_schemas


def build_langchain_tools() -> List[Any]:
    """
    Builds LangChain StructuredTool objects from cached MCP tool schemas.
    Uses synchronous subprocess calls for tool execution (no anyio conflict).
    """
    raw_tools = get_cached_tool_schemas()
    langchain_tools = []
    
    for raw_tool in raw_tools:
        tool_name = raw_tool.get("name", "")
        tool_desc = raw_tool.get("description", "")
        input_schema = raw_tool.get("inputSchema") or {}
        if isinstance(input_schema, dict):
            input_schema = input_schema
        else:
            input_schema = {}
        
        def _make_tool(name: str, desc: str, schema: dict):
            async def _invoke(**kwargs) -> str:
                # Run synchronous subprocess in thread pool to not block the event loop
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, call_mcp_tool_sync, name, kwargs)
            
            # Build Pydantic model for strong typing
            fields = {}
            properties = schema.get("properties", {})
            required_fields = schema.get("required", [])
            
            for prop_name, prop_data in properties.items():
                ptype = str
                t = prop_data.get("type", "string")
                if t == "integer": ptype = int
                elif t == "number": ptype = float
                elif t == "boolean": ptype = bool
                elif t == "object": ptype = dict
                elif t == "array": ptype = list
                
                desc_field = prop_data.get("description", "")
                if prop_name in required_fields:
                    fields[prop_name] = (ptype, Field(..., description=desc_field))
                else:
                    fields[prop_name] = (ptype, Field(None, description=desc_field))
            
            ArgsModel = create_model(f"{name.title().replace('_','')}Args", __base__=BaseModel, **fields)
            
            return StructuredTool.from_function(
                coroutine=_invoke,
                name=name,
                description=desc,
                args_schema=ArgsModel
            )
        
        langchain_tools.append(_make_tool(tool_name, tool_desc, input_schema))
    
    return langchain_tools


# Compatibility shim - same interface as before
class DbtMcpIntegrationShim:
    async def get_langchain_tools(self) -> List[Any]:
        return build_langchain_tools()

mcp_integration = DbtMcpIntegrationShim()
