import asyncio
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

load_dotenv(r"c:\Users\bhosa\Desktop\Langchain-V1-Crash-Course-main\.env")

async def main():
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    print("Calling OpenAI API directly (no tools)...")
    try:
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage("Say hello")]),
            timeout=15.0
        )
        print("Success! Response:", response.content)
    except asyncio.TimeoutError:
        print("TIMEOUT - OpenAI API unreachable!")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
