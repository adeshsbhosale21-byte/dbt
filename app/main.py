from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import json
import asyncio
import os
import uuid
from typing import Dict, List
from app.agent import app as agent_app
from app.security import apply_guardrails
from app.logger import get_logger, request_id_var

logger = get_logger("main")

# Initialize FastAPI for backend orchestration
app = FastAPI(title="dbt MCP Analytics Agent Backend")

# Session Storage Paths
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
METADATA_PATH = os.path.join(SESSIONS_DIR, "sessions_meta.json")

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

def load_metadata() -> List[Dict]:
    if os.path.exists(METADATA_PATH):
        try:
            with open(METADATA_PATH, "r") as f:
                return json.load(f)
        except:
            return []
    return []

def save_metadata(meta: List[Dict]):
    with open(METADATA_PATH, "w") as f:
        json.dump(meta, f, indent=2)

def save_history(thread_id: str, messages: List):
    serializable = []
    for m in messages:
        # Handle tuple format (role, content) from state
        if isinstance(m, tuple):
            serializable.append({"role": m[0], "content": m[1]})
        # Handle LangChain BaseMessage objects
        elif hasattr(m, "type"):
            role = "human" if m.type == "human" else "ai"
            serializable.append({"role": role, "content": m.content})
        else:
            serializable.append(m)
            
    try:
        with open(os.path.join(SESSIONS_DIR, f"{thread_id}.json"), "w") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save history for {thread_id}: {e}")

def load_history(thread_id: str) -> List:
    path = os.path.join(SESSIONS_DIR, f"{thread_id}.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            return []
    return []

# Allow React Frontend connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from langchain_core.messages import ToolMessage

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    logger.info("New Client Connected via WebSocket")
    
    current_thread_id = str(uuid.uuid4())
    is_processing = False
    session_messages = []
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data_json = json.loads(raw_data)
                cmd_type = data_json.get("type")
                
                # Generate RID for this transaction
                rid = f"REQ-{uuid.uuid4().hex[:6]}"
                request_id_var.set(rid)
                
                logger.debug(f"Handling websocket message: {cmd_type or 'chat'}")

                # Handling Session Commands
                if cmd_type == "list_sessions":
                    logger.info("Client requested session list")
                    await websocket.send_json({"type": "sessions_list", "content": load_metadata()})
                    continue
                elif cmd_type == "load_session":
                    current_thread_id = data_json.get("id")
                    logger.info(f"Client loading session: {current_thread_id}")
                    session_messages = load_history(current_thread_id)
                    await websocket.send_json({"type": "history_loaded", "content": session_messages})
                    continue
                elif cmd_type == "new_chat":
                    logger.info("Initializing new chat session")
                    current_thread_id = str(uuid.uuid4())
                    session_messages = []
                    await websocket.send_json({"type": "status", "content": "ready"})
                    continue
                
                # Handling Tool Approval Responses
                elif cmd_type == "approval_response":
                    value = data_json.get("value")
                    logger.info(f"Received tool approval response: {value}")
                    config = {"configurable": {"thread_id": current_thread_id}}
                    state = agent_app.get_state(config)
                    
                    if value == "yes":
                        logger.debug("Resuming graph execution after approval")
                        await run_and_stream(None, config)
                    else:
                        logger.info("User REJECTED tool execution. Cancelling...")
                        # Cancel: Inject ToolMessages with cancellation content
                        last_msg = state.values["messages"][-1]
                        cancellation_msgs = [
                            ToolMessage(content="Action cancelled by user.", tool_call_id=tc["id"]) 
                            for tc in last_msg.tool_calls
                        ]
                        agent_app.update_state(config, {"messages": cancellation_msgs})
                        session_messages.append({"role": "ai", "content": "Action cancelled."})
                        await websocket.send_json({"type": "agent_state", "content": "Action cancelled."})
                        save_metadata(meta)
                        await websocket.send_json({"type": "sessions_list", "content": meta})
                    continue
                
                elif cmd_type == "delete_session":
                    sid = data_json.get("id")
                    logger.info(f"Deleting session: {sid}")
                    meta = load_metadata()
                    meta = [m for m in meta if m["id"] != sid]
                    save_metadata(meta)
                    # Delete file
                    filepath = os.path.join(SESSIONS_DIR, f"{sid}.json")
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    await websocket.send_json({"type": "sessions_list", "content": meta})
                    if sid == current_thread_id:
                        current_thread_id = str(uuid.uuid4())
                        session_messages = []
                    continue

                chat_text = data_json.get("content", raw_data)
            except json.JSONDecodeError:
                chat_text = raw_data

            if is_processing and chat_text.lower() != "stop":
                logger.warning("Rejecting message - Agent is BUSY")
                await websocket.send_json({"type": "error", "content": "Agent is busy."})
                continue
            if chat_text.lower() == "stop": 
                logger.info("User sent STOP command")
                continue

            # --- MANUAL APPROVAL FALLBACK ---
            # If we are waiting for approval and user types "yes"/"approved"
            config = {"configurable": {"thread_id": current_thread_id}}
            state = agent_app.get_state(config)
            
            if state.next and "tools" in state.next:
                ans = chat_text.lower().strip()
                if ans in ["yes", "approve", "approved", "go ahead", "do it"]:
                    logger.info(f"Interpreting manual text '{ans}' as APPROVAL")
                    await run_and_stream(None, config)
                    continue
                elif ans in ["no", "cancel", "stop", "abort"]:
                    logger.info(f"Interpreting manual text '{ans}' as CANCELLATION")
                    # Inject ToolMessages with cancellation content
                    last_msg = state.values["messages"][-1]
                    cancellation_msgs = [
                        ToolMessage(content="Action cancelled by user.", tool_call_id=tc["id"]) 
                        for tc in last_msg.tool_calls
                    ]
                    agent_app.update_state(config, {"messages": cancellation_msgs})
                    session_messages.append({"role": "ai", "content": "Action cancelled."})
                    save_history(current_thread_id, session_messages)
                    await websocket.send_json({"type": "agent_state", "content": "Action cancelled."})
                    await websocket.send_json({"type": "status", "content": "ready"})
                    continue

            if not apply_guardrails(chat_text, "input"):
                await websocket.send_json({"type": "error", "content": "Security violation."})
                continue
            
            is_processing = True
            await websocket.send_json({"type": "status", "content": "busy"})
            
            # --- PIVOT PROTECTION ---
            # If we are currently interrupted (waiting for tool approval) and user sent a NEW message:
            # We must close the previous tool calls before adding the new human message.
            config = {"configurable": {"thread_id": current_thread_id}}
            state = agent_app.get_state(config)
            if state.next and "tools" in state.next:
                last_msg = state.values["messages"][-1]
                close_msgs = [
                    ToolMessage(content="User pivoted to a new question.", tool_call_id=tc["id"]) 
                    for tc in last_msg.tool_calls
                ]
                agent_app.update_state(config, {"messages": close_msgs})

            # Update Metadata if it's the first message
            meta = load_metadata()
            session_entry = next((m for m in meta if m["id"] == current_thread_id), None)
            if not session_entry:
                title = chat_text[:30] + ("..." if len(chat_text) > 30 else "")
                session_entry = {"id": current_thread_id, "title": title, "created_at": str(asyncio.get_event_loop().time())}
                meta.insert(0, session_entry)
                save_metadata(meta)
                await websocket.send_json({"type": "sessions_list", "content": meta})

            # Execute Graph Node
            session_messages.append({"role": "human", "content": chat_text})
            save_history(current_thread_id, session_messages)
            
            async def run_and_stream(input_val, conf):
                nonlocal session_messages, is_processing
                logger.debug(f"Entering graph stream: input={input_val}")
                try:
                    async for output in agent_app.astream(input_val, conf):
                        for node_name, state_output in output.items():
                            if node_name == "__metadata__": continue
                            logger.info(f"Graph Transition: -> {node_name}")
                            
                            if "messages" in state_output and state_output["messages"]:
                                latest_msg = state_output["messages"][-1]
                                if hasattr(latest_msg, "tool_calls") and latest_msg.tool_calls:
                                    logger.info(f"Node '{node_name}' requested TOOL CALLS: {[tc['name'] for tc in latest_msg.tool_calls]}")
                                    # Interrupt UI
                                    await websocket.send_json({
                                        "type": "approval_request",
                                        "tool": [tc['name'] for tc in latest_msg.tool_calls]
                                    })
                                    is_processing = False # Allow buttons to be clicked
                                    await websocket.send_json({"type": "status", "content": "waiting_approval"})
                                    return
                                else:
                                    content = latest_msg.content
                                    logger.debug(f"Node '{node_name}' generated content (length: {len(content)})")
                                    session_messages.append({"role": "ai", "content": content})
                                    save_history(current_thread_id, session_messages)
                                    await websocket.send_json({
                                        "type": "agent_state", "node": node_name, "content": content
                                    })
                    
                    logger.info("Graph execution completed successfully")
                    is_processing = False
                    await websocket.send_json({"type": "status", "content": "ready"})
                    await websocket.send_json({"type": "done"})
                except Exception as e:
                    logger.error(f"Graph execution failed: {e}", exc_info=True)
                    is_processing = False
                    await websocket.send_json({"type": "error", "content": f"Agent error: {str(e)}"})
            
            await run_and_stream({"messages": [("user", chat_text)]}, config)
            save_history(current_thread_id, session_messages)

    except WebSocketDisconnect:
        logger.info(f"WebSocket Client Disconnected: {current_thread_id}")

# Serve the React HTML frontend
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")

@app.get("/")
def redirect_to_app():
    return RedirectResponse(url="/app/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, workers=4)
