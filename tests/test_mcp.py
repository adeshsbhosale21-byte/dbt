import asyncio
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
local_dbt_test_dir = os.path.join(current_dir, "..", "local_dbt_test")
os.environ["DBT_PROJECT_DIR"] = local_dbt_test_dir
os.environ["DBT_PROFILES_DIR"] = local_dbt_test_dir

from mcp_client import mcp_integration

async def test_mcp_connection():
    try:
        print("1. Starting dbt-mcp server and establishing MCP session...")
        await mcp_integration.connect()
        
        print("\n2. Asking the server for available tools...")
        tools = await mcp_integration.session.list_tools()
        print(f"Success! Discovered {len(tools.tools)} dynamic tools from dbt-mcp.")
        
        for t in tools.tools:
            print(f"  - {t.name}")
            
        print("\n3. Testing execution of 'get_node_details_dev' tool (Reads local manifest)...")
        result1 = await mcp_integration.session.call_tool(
            "get_node_details_dev", 
            arguments={"node_id": "seed.local_dbt_test.fct_sales"}
        )
        print("\nResult from dbt-mcp (manifest details):")
        print(str(result1.content[0].text)[:150] + "...\n")
        
        print("\n4. Testing execution of 'show' tool (Executing SQL via dbt show)...")
        query = "select sum(revenue) as total_revenue from {{ ref('fct_sales') }}"
        print(f"Executing inline jinja SQL: {query}")
        result2 = await mcp_integration.session.call_tool(
            "show", 
            arguments={"sql_query": query}
        )
        print("\nResult from DuckDB via dbt-mcp:")
        print(result2.content[0].text)

    except Exception as e:
        print(f"Error during testing: {e}")
    finally:
        await mcp_integration.close()
        print("\nClosed connection.")

if __name__ == "__main__":
    asyncio.run(test_mcp_connection())
