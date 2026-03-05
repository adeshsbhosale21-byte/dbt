import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.mcp_client import mcp_integration

async def test_discovery():
    print("Starting dbt-mcp tool discovery test...")
    try:
        tools = await mcp_integration.get_langchain_tools()
        print(f"Success! Found {len(tools)} tools.")
        for t in tools:
            print(f" - {t.name}: {t.description[:50]}...")
    except Exception as e:
        print(f"Discovery failed: {e}")

if __name__ == "__main__":
    # Ensure DBT_PROJECT_DIR is set
    os.environ["DBT_PROJECT_DIR"] = os.path.abspath("local_dbt_test")
    asyncio.run(test_discovery())
