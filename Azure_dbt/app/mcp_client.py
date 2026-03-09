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


class PersistentMcpClient:
    """
    Maintains a single long-running dbt-mcp subprocess to avoid the 15s
    startup/manifest-loading penalty on every tool call.
    """
    def __init__(self):
        self.proc = None
        self.lock = asyncio.Lock()
        self._initialized = False

    async def _start_if_needed(self):
        if self.proc and self.proc.poll() is None:
            return

        logger.info("Starting persistent dbt-mcp subprocess...")
        scripts_dir = _get_venv_scripts()
        env = os.environ.copy()
        env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
        
        for key, value in os.environ.items():
            if key.startswith("DBT_") or key.startswith("DB_") or key.startswith("DISABLE_"):
                env[key] = value

        dbt_project_dir = os.path.abspath(_get_dbt_project_dir())
        env["DBT_PROJECT_DIR"] = dbt_project_dir
        env["DBT_PROFILES_DIR"] = dbt_project_dir
        
        if not env.get("DBT_PATH"):
            env["DBT_PATH"] = os.path.join(scripts_dir, "dbt.exe" if os.name == "nt" else "dbt")

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dbt_mcp.main", "--project-dir", dbt_project_dir, "--profiles-dir", dbt_project_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1 # Line buffered
        )

        # MCP Init Handshake
        init_messages = [
            {"jsonrpc": "2.0", "id": "init_1", "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent", "version": "1.0"}
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        ]
        
        for msg in init_messages:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            if "id" in msg:
                # Wait for init response
                self.proc.stdout.readline()

        self._initialized = True
        logger.info("Persistent dbt-mcp initialized and ready.")

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        async with self.lock:
            await self._start_if_needed()
            
            request_id = f"call_{tool_name}_{os.urandom(4).hex()}"
            msg = {
                "jsonrpc": "2.0", 
                "id": request_id, 
                "method": "tools/call", 
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
                
                # Simple line-by-line listener for our ID
                # In a real heavy-traffic app we'd use a reader task + futures,
                # but for an agent context this synchronous-style read over pipe is fine.
                while True:
                    line = self.proc.stdout.readline()
                    if not line: break
                    data = json.loads(line)
                    if data.get("id") == request_id:
                        result = data.get("result", {})
                        if result.get("isError"):
                            return f"Tool Error: {result.get('content')}"
                        content = result.get("content", [])
                        if isinstance(content, list):
                            return " ".join(c.get("text", "") for c in content)
                        return str(content)
            except Exception as e:
                logger.error(f"Persistent call error: {e}")
                if self.proc: self.proc.kill()
                return f"Error communicating with dbt-mcp: {e}"

        return "No response from tool."

_persistent_client = PersistentMcpClient()

def fetch_mcp_tool_schemas() -> list:
    """Uses a one-off call for discovery (only happens once)."""
    scripts_dir = _get_venv_scripts()
    env = os.environ.copy()
    env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
    for key, value in os.environ.items():
        if key.startswith("DBT_") or key.startswith("DB_") or key.startswith("DISABLE_"):
            env[key] = value
    dbt_project_dir = os.path.abspath(_get_dbt_project_dir())
    
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05","capabilities": {},"clientInfo": {"name": "agent", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    ]
    stdin_data = "\n".join(json.dumps(m) for m in messages) + "\n"
    
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "dbt_mcp.main", "--project-dir", dbt_project_dir, "--profiles-dir", dbt_project_dir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )
        stdout, _ = proc.communicate(input=stdin_data, timeout=30)
        for line in stdout.splitlines():
            data = json.loads(line)
            if data.get("id") == 2: return data.get("result", {}).get("tools", [])
    except: pass
    return []

_cached_tool_schemas = None
def get_cached_tool_schemas() -> list:
    global _cached_tool_schemas
    if _cached_tool_schemas is None:
        _cached_tool_schemas = fetch_mcp_tool_schemas()
    return _cached_tool_schemas

def build_langchain_tools() -> List[Any]:
    raw_tools = get_cached_tool_schemas()
    langchain_tools = []
    
    for raw_tool in raw_tools:
        tool_name = raw_tool.get("name", "")
        tool_desc = raw_tool.get("description", "")
        input_schema = raw_tool.get("inputSchema") or {}
        
        def _make_tool(name: str, desc: str, schema: dict):
            list_params = [p for p, d in schema.get("properties", {}).items() if d.get("type") == "array"]

            async def _invoke(**kwargs) -> str:
                for lp in list_params:
                    if lp in kwargs and isinstance(kwargs[lp], str):
                        kwargs[lp] = [kwargs[lp]]
                return await _persistent_client.call_tool(name, kwargs)
            
            fields = {}
            required = schema.get("required", [])
            for p_name, p_data in schema.get("properties", {}).items():
                ptype = {"string":str,"integer":int,"number":float,"boolean":bool,"array":list,"object":dict}.get(p_data.get("type"), str)
                if p_name in required:
                    fields[p_name] = (ptype, Field(..., description=p_data.get("description", "")))
                else:
                    fields[p_name] = (ptype, Field(None, description=p_data.get("description", "")))
            
            ArgsModel = create_model(f"{name.title().replace('_','')}Args", __base__=BaseModel, **fields)
            return StructuredTool.from_function(coroutine=_invoke, name=name, description=desc, args_schema=ArgsModel)
        
        langchain_tools.append(_make_tool(tool_name, tool_desc, input_schema))
    return langchain_tools

_cached_langchain_tools = None
class DbtMcpIntegrationShim:
    async def get_langchain_tools(self) -> List[Any]:
        global _cached_langchain_tools
        if _cached_langchain_tools is None:
            _cached_langchain_tools = build_langchain_tools()
        return _cached_langchain_tools

mcp_integration = DbtMcpIntegrationShim()
