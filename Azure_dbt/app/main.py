from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import json
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Dict, List
from app.agent import app as agent_app, compile_agent
from app.security import apply_guardrails
from app.logger import get_logger, request_id_var

logger = get_logger("main")

# ─── Azure PostgreSQL Session Persistence ───
PG_CONN_STR = os.environ.get("AZURE_PG_CONNECTION_STRING")
pg_pool = None

async def init_pg():
    """Initialize PostgreSQL connection pool and create tables."""
    global pg_pool
    if not PG_CONN_STR:
        logger.info("No AZURE_PG_CONNECTION_STRING set. Using local file storage.")
        return
    try:
        import asyncpg
        pg_pool = await asyncpg.create_pool(PG_CONN_STR, min_size=1, max_size=5)
        async with pg_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS sessions_meta (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS session_history (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_session_history_sid ON session_history(session_id)
            ''')
        logger.info("Azure PostgreSQL initialized successfully with tables created.")
    except Exception as e:
        logger.error(f"PostgreSQL init failed (falling back to local files): {e}")
        pg_pool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager for FastAPI (replaces startup/shutdown events)."""
    await init_pg()
    
    # Initialize Persistent Agent Memory
    if PG_CONN_STR:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool
            from psycopg import AsyncConnection
            from app.agent import compile_agent
            
            # 1. Setup tables using a separate autocommit connection (fixes CREATE INDEX error)
            async with await AsyncConnection.connect(PG_CONN_STR, autocommit=True) as conn:
                temp_saver = AsyncPostgresSaver(conn)
                await temp_saver.setup()
            
            # 2. Initialize the pooled checkpointer for the application
            async with AsyncConnectionPool(conninfo=PG_CONN_STR, max_size=10, timeout=30) as pool:
                checkpointer = AsyncPostgresSaver(pool)
                
                # Re-compile agent with the persistent checkpointer
                from app import agent as agent_module
                agent_module.app = compile_agent(checkpointer)
                logger.info("Agent persistent checkpointer (PostgreSQL) initialized.")
                
                yield # Server runs here
        except Exception as e:
            logger.error(f"Failed to initialize PostgresSaver lifespan: {e}")
            yield
    else:
        yield

# Initialize FastAPI with lifespan
app = FastAPI(title="dbt MCP Analytics Agent (Azure)", lifespan=lifespan)

# Local File Fallback
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
METADATA_PATH = os.path.join(SESSIONS_DIR, "sessions_meta.json")

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# ─── CRUD Functions (PostgreSQL primary, local file fallback) ───

async def load_metadata() -> List[Dict]:
    if pg_pool:
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT id, title, created_at FROM sessions_meta ORDER BY created_at DESC")
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"PG load_metadata failed: {e}")
    # Local fallback
    if os.path.exists(METADATA_PATH):
        try:
            with open(METADATA_PATH, "r") as f:
                return json.load(f)
        except:
            return []
    return []

async def save_metadata_entry(session_id: str, title: str, created_at: str):
    if pg_pool:
        try:
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO sessions_meta (id, title, created_at) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
                    session_id, title, created_at
                )
                logger.debug(f"PG saved metadata entry: {session_id}")
                return
        except Exception as e:
            logger.error(f"PG save_metadata_entry failed: {e}")
    # Local fallback
    meta = await load_metadata()
    if not any(m["id"] == session_id for m in meta):
        meta.insert(0, {"id": session_id, "title": title, "created_at": created_at})
        with open(METADATA_PATH, "w") as f:
            json.dump(meta, f, indent=2)

async def save_history(thread_id: str, messages: List):
    serializable = []
    for m in messages:
        if isinstance(m, tuple):
            serializable.append({"role": m[0], "content": m[1]})
        elif hasattr(m, "type"):
            role = "human" if m.type == "human" else "ai"
            serializable.append({"role": role, "content": m.content})
        else:
            serializable.append(m)

    if pg_pool:
        try:
            async with pg_pool.acquire() as conn:
                # Clear existing and re-insert (simple approach for ordered history)
                await conn.execute("DELETE FROM session_history WHERE session_id = $1", thread_id)
                for msg in serializable:
                    await conn.execute(
                        "INSERT INTO session_history (session_id, role, content) VALUES ($1, $2, $3)",
                        thread_id, msg["role"], msg["content"]
                    )
                logger.debug(f"PG saved history: {thread_id} ({len(serializable)} messages)")
                return
        except Exception as e:
            logger.error(f"PG save_history failed for {thread_id}: {e}")

    # Local fallback
    try:
        with open(os.path.join(SESSIONS_DIR, f"{thread_id}.json"), "w") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save local history for {thread_id}: {e}")

async def load_history(thread_id: str) -> List:
    if pg_pool:
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT role, content FROM session_history WHERE session_id = $1 ORDER BY id",
                    thread_id
                )
                return [{"role": r["role"], "content": r["content"]} for r in rows]
        except Exception as e:
            logger.warning(f"PG load_history failed for {thread_id}: {e}")
    # Local fallback
    path = os.path.join(SESSIONS_DIR, f"{thread_id}.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            return []
    return []

async def delete_session(session_id: str):
    if pg_pool:
        try:
            async with pg_pool.acquire() as conn:
                await conn.execute("DELETE FROM session_history WHERE session_id = $1", session_id)
                await conn.execute("DELETE FROM sessions_meta WHERE id = $1", session_id)
                logger.info(f"PG deleted session: {session_id}")
                return
        except Exception as e:
            logger.error(f"PG delete_session failed: {e}")
    # Local fallback
    filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    if os.path.exists(filepath):
        os.remove(filepath)

# CORS
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
                
                rid = f"REQ-{uuid.uuid4().hex[:6]}"
                request_id_var.set(rid)
                
                logger.debug(f"Handling websocket message: {cmd_type or 'chat'}")

                if cmd_type == "list_sessions":
                    logger.info("Client requested session list")
                    meta = await load_metadata()
                    await websocket.send_json({"type": "sessions_list", "content": meta})
                    continue
                elif cmd_type == "load_session":
                    current_thread_id = data_json.get("id")
                    logger.info(f"Client loading session: {current_thread_id}")
                    session_messages = await load_history(current_thread_id)
                    await websocket.send_json({"type": "history_loaded", "content": session_messages})
                    continue
                elif cmd_type == "new_chat":
                    logger.info("Initializing new chat session")
                    current_thread_id = str(uuid.uuid4())
                    session_messages = []
                    await websocket.send_json({"type": "status", "content": "ready"})
                    continue
                
                elif cmd_type == "approval_response":
                    value = data_json.get("value")
                    logger.info(f"Received tool approval response: {value}")
                    config = {"configurable": {"thread_id": current_thread_id}}
                    state = agent_app.get_state(config)
                    
                    if value == "yes":
                        await run_and_stream(None, config)
                    else:
                        last_msg = state.values["messages"][-1]
                        cancellation_msgs = [
                            ToolMessage(content="Action cancelled by user.", tool_call_id=tc["id"]) 
                            for tc in last_msg.tool_calls
                        ]
                        agent_app.update_state(config, {"messages": cancellation_msgs})
                        session_messages.append({"role": "ai", "content": "Action cancelled."})
                        await save_history(current_thread_id, session_messages)
                        await websocket.send_json({"type": "agent_state", "content": "Action cancelled."})
                        await websocket.send_json({"type": "status", "content": "ready"})
                    continue
                
                elif cmd_type == "delete_session":
                    sid = data_json.get("id")
                    logger.info(f"Deleting session: {sid}")
                    await delete_session(sid)
                    meta = await load_metadata()
                    await websocket.send_json({"type": "sessions_list", "content": meta})
                    if sid == current_thread_id:
                        current_thread_id = str(uuid.uuid4())
                        session_messages = []
                    continue

                chat_text = data_json.get("content", raw_data)
            except json.JSONDecodeError:
                chat_text = raw_data

            if is_processing and chat_text.lower() != "stop":
                await websocket.send_json({"type": "error", "content": "Agent is busy."})
                continue
            if chat_text.lower() == "stop": 
                continue

            # Manual Approval Fallback
            config = {"configurable": {"thread_id": current_thread_id}}
            state = agent_app.get_state(config)
            
            if state.next and "tools" in state.next:
                ans = chat_text.lower().strip()
                if ans in ["yes", "approve", "approved", "go ahead", "do it"]:
                    await run_and_stream(None, config)
                    continue
                elif ans in ["no", "cancel", "stop", "abort"]:
                    last_msg = state.values["messages"][-1]
                    cancellation_msgs = [
                        ToolMessage(content="Action cancelled by user.", tool_call_id=tc["id"]) 
                        for tc in last_msg.tool_calls
                    ]
                    agent_app.update_state(config, {"messages": cancellation_msgs})
                    session_messages.append({"role": "ai", "content": "Action cancelled."})
                    await save_history(current_thread_id, session_messages)
                    await websocket.send_json({"type": "agent_state", "content": "Action cancelled."})
                    await websocket.send_json({"type": "status", "content": "ready"})
                    continue

            if not apply_guardrails(chat_text, "input"):
                await websocket.send_json({"type": "error", "content": "Security violation."})
                continue
            
            is_processing = True
            await websocket.send_json({"type": "status", "content": "busy"})
            
            # Pivot Protection
            state = agent_app.get_state(config)
            if state.next and "tools" in state.next:
                last_msg = state.values["messages"][-1]
                close_msgs = [
                    ToolMessage(content="User pivoted to a new question.", tool_call_id=tc["id"]) 
                    for tc in last_msg.tool_calls
                ]
                agent_app.update_state(config, {"messages": close_msgs})

            # Save metadata on first message
            meta = await load_metadata()
            session_entry = next((m for m in meta if m["id"] == current_thread_id), None)
            if not session_entry:
                title = chat_text[:30] + ("..." if len(chat_text) > 30 else "")
                created_at = str(asyncio.get_event_loop().time())
                await save_metadata_entry(current_thread_id, title, created_at)
                meta = await load_metadata()
                await websocket.send_json({"type": "sessions_list", "content": meta})

            session_messages.append({"role": "human", "content": chat_text})
            await save_history(current_thread_id, session_messages)
            
            async def run_and_stream(input_val, conf):
                nonlocal session_messages, is_processing
                try:
                    async for output in agent_app.astream(input_val, conf):
                        for node_name, state_output in output.items():
                            if node_name == "__metadata__": continue
                            logger.info(f"Graph Transition: -> {node_name}")
                            
                            if "messages" in state_output and state_output["messages"]:
                                latest_msg = state_output["messages"][-1]
                                if hasattr(latest_msg, "tool_calls") and latest_msg.tool_calls:
                                    await websocket.send_json({
                                        "type": "approval_request",
                                        "tool": [tc['name'] for tc in latest_msg.tool_calls]
                                    })
                                    is_processing = False
                                    await websocket.send_json({"type": "status", "content": "waiting_approval"})
                                    return
                                else:
                                    content = latest_msg.content
                                    session_messages.append({"role": "ai", "content": content})
                                    await save_history(current_thread_id, session_messages)
                                    await websocket.send_json({
                                        "type": "agent_state", "node": node_name, "content": content
                                    })
                    
                    is_processing = False
                    await websocket.send_json({"type": "status", "content": "ready"})
                    await websocket.send_json({"type": "done"})
                except Exception as e:
                    logger.error(f"Graph execution failed: {e}", exc_info=True)
                    is_processing = False
                    await websocket.send_json({"type": "error", "content": f"Agent error: {str(e)}"})
            
            await run_and_stream({"messages": [("user", chat_text)]}, config)
            await save_history(current_thread_id, session_messages)

    except WebSocketDisconnect:
        logger.info(f"WebSocket Client Disconnected: {current_thread_id}")

# Serve Frontend
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")

@app.get("/")
def redirect_to_app():
    return RedirectResponse(url="/app/index.html")

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "dbt-mcp-agent-azure",
        "storage": "postgresql" if pg_pool else "local_files"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, workers=4)
