import os
import sys
import json
import subprocess

def discover_tools():
    scripts_dir = os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")
    dbt_project_dir = os.path.abspath("c:/Users/bhosa/Desktop/Langchain-V1-Crash-Course-main/nextin-rag-main/dbt_mcp_agent/Azure_dbt/local_dbt_test")
    
    env = os.environ.copy()
    env["DBT_PROJECT_DIR"] = dbt_project_dir
    env["DBT_PROFILES_DIR"] = dbt_project_dir
    env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "discovery", "version": "1.0"}
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    ]
    
    stdin_data = "\n".join(json.dumps(m) for m in messages) + "\n"
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "dbt_mcp.main", "--project-dir", dbt_project_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )
    stdout, stderr = proc.communicate(input=stdin_data)
    
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
            if data.get("id") == 2:
                print(json.dumps(data.get("result", {}).get("tools", []), indent=2))
        except:
            continue

if __name__ == "__main__":
    discover_tools()
