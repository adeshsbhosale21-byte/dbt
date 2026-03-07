import os
import sys

# Force local backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.mcp_client import get_cached_tool_schemas

def test():
    schemas = get_cached_tool_schemas()
    print(schemas)

if __name__ == "__main__":
    test()
