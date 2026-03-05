"""Debug script to see raw output from dbt-mcp subprocess"""
import sys
import os
import json
import subprocess

scripts_dir = os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")
env = os.environ.copy()
env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
dbt_project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "local_dbt_test"))
env["DBT_PROJECT_DIR"] = dbt_project_dir
env["DBT_PROFILES_DIR"] = dbt_project_dir

messages = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }},
    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
]

stdin_data = "\n".join(json.dumps(m) for m in messages) + "\n"
print("Sending to dbt-mcp:")
print(stdin_data)

result = subprocess.run(
    [sys.executable, "-m", "dbt_mcp.main", "--project-dir", dbt_project_dir],
    input=stdin_data,
    capture_output=True,
    text=True,
    timeout=15,
    env=env
)

print("\nSTDOUT:")
print(repr(result.stdout[:2000]))
print("\nSTDERR (first 500 chars):")
print(result.stderr[:500])
