import os, json, glob, uuid
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import redis.asyncio as redis

# ----------------------------
# ---- CONFIGURATION ----
# ----------------------------
AGENTS_DIR = os.getenv("AGENTS_DIR", "/repo/agents")
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")
CALLBACK_SECRET = os.getenv("CALLBACK_SECRET", "dev-secret")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
BACKEND_INTERNAL_BASE = os.getenv("BACKEND_INTERNAL_BASE", "http://backend:8000")

# ----------------------------
# ---- FASTAPI SETUP ----
# ----------------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # for local vite frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

r = redis.from_url(REDIS_URL, decode_responses=True)


# ----------------------------
# ---- MODELS ----
# ----------------------------
class ChatRequest(BaseModel):
    agent_id: str
    inputs: Dict


# ----------------------------
# ---- HELPER FUNCTIONS ----
# ----------------------------
def load_agents():
    """Load all agents from the configured directory."""
    agents = []
    for p in glob.glob(f"{AGENTS_DIR}/*.json"):
        with open(p) as f:
            data = json.load(f)
            if isinstance(data, list):
                agents.extend(data)
            else:
                agents.append(data)
    return agents


async def create_transaction(description: str, agent_name: str, message: str):
    """Create and index a new transaction record."""
    txn_id = str(uuid.uuid4())
    txn_key = f"txn:{txn_id}"
    txn_data = {
        "id": txn_id,
        "description": description,
        "research": "",
        "archetype": "",
        "value_prop": "",
        "buyer_profile": "",
        "business_case": "",
        "solution": "",
        "history": [{"agent": agent_name, "message": message}],
    }
    await r.set(txn_key, json.dumps(txn_data))
    await r.hset("txn_index", description.lower(), txn_id)
    return txn_data


async def find_transaction(search_text: str):
    """Finds a transaction whose description contains the given search text."""
    search_text = search_text.lower().strip()
    all_descs = await r.hkeys("txn_index")
    for desc in all_descs:
        if search_text in desc:
            txn_id = await r.hget("txn_index", desc)
            txn_data = await r.get(f"txn:{txn_id}")
            return json.loads(txn_data) if txn_data else None
    return None


# ----------------------------
# ---- ROUTES ----
# ----------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/agents")
def list_agents():
    """Return agents grouped by sales stage."""
    grouped = {}
    for a in load_agents():
        grouped.setdefault(a["stage"], []).append(a)
    return grouped


@app.post("/chat/start")
async def start_chat(req: ChatRequest):
    """Start a conversation by invoking an n8n webhook for the initial agent."""
    convo_id = str(uuid.uuid4())
    agents = {a["id"]: a for a in load_agents()}
    agent = agents[req.agent_id]

    callback_url = (
        f"{BACKEND_INTERNAL_BASE}/webhooks/n8n/callback?"
        f"convo={convo_id}&secret={CALLBACK_SECRET}"
    )
    payload = {**req.inputs, "callback_url": callback_url}

    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(N8N_BASE_URL + agent["webhook_path"], json=payload)

    return {"conversation_id": convo_id}


@app.websocket("/ws/{convo_id}")
async def ws_endpoint(ws: WebSocket, convo_id: str):
    """Stream chat responses via Redis pub/sub."""
    await ws.accept()
    key = f"chat:{convo_id}"
    sub = r.pubsub()
    await sub.subscribe(key)
    try:
        await ws.send_text(json.dumps({"type": "status", "message": "connected"}))
        async for msg in sub.listen():
            if msg["type"] == "message":
                await ws.send_text(msg["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await sub.unsubscribe(key)
        await sub.close()


@app.post("/chat/send")
async def chat_send(request: Request):
    """Main chat endpoint — handles start, recall, and orchestrator routing."""
    data = await request.json()
    convo = data.get("conversation_id")
    message = data.get("message")
    agent_id = "orchestrator"  # always go through orchestrator

    txn_key = f"txn_for_convo:{convo}"

    # ---- START NEW TRANSACTION ----
    if message.lower().startswith("start"):
        desc = message.split(" ", 1)[1] if " " in message else "New Deal"
        txn_id = str(uuid.uuid4())
        txn = {
            "id": txn_id,
            "description": desc,
            "research": "",
            "archetype": "",
            "value_prop": "",
            "buyer_profile": "",
            "business_case": "",
            "solution": "",
            "history": [],
        }
        await r.set(f"txn:{txn_id}", json.dumps(txn))
        await r.set(txn_key, txn_id)
        await r.hset("txn_index", desc.lower(), txn_id)

        await r.publish(
            f"chat:{convo}",
            json.dumps(
                {"type": "n8n_result", "data": {"result": f"Started new transaction: {desc}"}}
            ),
        )
        return {"ok": True}

    # ---- RECALL TRANSACTION ----
    if message.lower().startswith("recall"):
        search = message.split(" ", 1)[1].strip().lower()
        txn = await find_transaction(search)
        if not txn:
            await r.publish(
                f"chat:{convo}",
                json.dumps(
                    {
                        "type": "n8n_result",
                        "data": {"result": f"No transaction found for '{search}'"},
                    }
                ),
            )
            return {"ok": True}

        await r.set(txn_key, txn["id"])
        await r.publish(
            f"chat:{convo}",
            json.dumps(
                {
                    "type": "n8n_result",
                    "data": {"result": f"Recalled transaction: {txn['description']}"},
                }
            ),
        )
        return {"ok": True}

    # ---- NORMAL MESSAGE → Orchestrator ----
    txn_id = await r.get(txn_key)
    txn_state = json.loads(await r.get(f"txn:{txn_id}")) if txn_id else None

    # Record user message into history if possible
    if txn_state:
        txn_state["history"].append({"sender": "user", "message": message})
        await r.set(f"txn:{txn_id}", json.dumps(txn_state))

    payload = {
        "message": message,
        "conversation_id": convo,
        "transaction_id": txn_id,
        "state": txn_state,
        "callback_url": f"{BACKEND_INTERNAL_BASE}/webhooks/n8n/callback?convo={convo}&secret={CALLBACK_SECRET}",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{N8N_BASE_URL}/webhook/orchestrator", json=payload)
            resp.raise_for_status()
    except Exception as e:
        await r.publish(
            f"chat:{convo}",
            json.dumps(
                {
                    "type": "error",
                    "data": {"result": f"Failed to contact orchestrator: {str(e)}"},
                }
            ),
        )

    return {"ok": True}


@app.get("/transactions")
async def list_transactions():
    """Return all transactions for debugging / inspection."""
    keys = await r.keys("txn:*")
    txns = []
    for key in keys:
        data = await r.get(key)
        if data:
            txns.append(json.loads(data))
    return {"transactions": txns}


@app.post("/webhooks/n8n/callback")
async def n8n_callback(request: Request, convo: str, secret: str):
    """Handle callbacks from orchestrator or specialist agents."""
    if secret != CALLBACK_SECRET:
        return Response(status_code=403)

    query = dict(request.query_params)
    headers = dict(request.headers)
    
    # Log everything for debugging
    try:
        data = await request.json()
    except Exception:
        data = {"error": "Invalid JSON body"}

    print("\n================ N8N CALLBACK ================")
    print("🔹 Query Params:", json.dumps(dict(request.query_params), indent=2))
    print("🔹 Body:", json.dumps(data, indent=2))
    print("==============================================\n")

    # data = await request.json()
    agent_type = data.get("agent_type")  # e.g. "research", "solution"
    result_text = data.get("result")
    txn_id = await r.get(f"txn_for_convo:{convo}") or data.get("transaction_id")

    if txn_id:
        txn_raw = await r.get(f"txn:{txn_id}")
        if txn_raw:
            txn = json.loads(txn_raw)
            txn["history"].append({"sender": agent_type or "agent", "message": result_text})
            if agent_type in txn:
                txn[agent_type] = result_text
            await r.set(f"txn:{txn_id}", json.dumps(txn))

    await r.publish(
        f"chat:{convo}",
        json.dumps({"type": "n8n_result", "data": {"result": result_text}}),
    )

    return {"ok": True}
