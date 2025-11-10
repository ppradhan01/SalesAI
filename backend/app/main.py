import os, json, glob, asyncio
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import redis.asyncio as redis

# ---- Config ----
AGENTS_DIR = os.getenv("AGENTS_DIR", "/repo/agents")
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")
CALLBACK_SECRET = os.getenv("CALLBACK_SECRET", "dev-secret")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
# IMPORTANT: for local docker, internal callback should hit the backend container (not localhost)
BACKEND_INTERNAL_BASE = os.getenv("BACKEND_INTERNAL_BASE", "http://backend:8000")

# ---- App ----
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

r = redis.from_url(REDIS_URL, decode_responses=True)

class ChatRequest(BaseModel):
    agent_id: str
    inputs: Dict

def load_agents():
    agents = []
    for p in glob.glob(f"{AGENTS_DIR}/*.json"):
        with open(p) as f:
            agents.append(json.load(f))
    return agents

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/agents")
def list_agents():
    grouped = {"targeting":[], "origination":[], "progression":[], "growth":[]}
    for a in load_agents():
        grouped.setdefault(a["stage"], []).append(a)
    return grouped

@app.post("/chat/start")
async def start_chat(req: ChatRequest):
    import uuid
    convo_id = str(uuid.uuid4())
    agents = {a["id"]: a for a in load_agents()}
    agent = agents[req.agent_id]

    # callback must be reachable by n8n container → use BACKEND_INTERNAL_BASE
    callback_url = f"{BACKEND_INTERNAL_BASE}/webhooks/n8n/callback?convo={convo_id}&secret={CALLBACK_SECRET}"
    payload = {**req.inputs, "callback_url": callback_url}

    async with httpx.AsyncClient(timeout=30) as client:
        # this will 2xx only if a webhook exists in n8n; that's fine, we’ll add it later.
        await client.post(N8N_BASE_URL + agent["webhook_path"], json=payload)
    return {"conversation_id": convo_id}

@app.websocket("/ws/{convo_id}")
async def ws_endpoint(ws: WebSocket, convo_id: str):
    await ws.accept()
    key = f"chat:{convo_id}"
    sub = r.pubsub()
    await sub.subscribe(key)
    try:
        await ws.send_text(json.dumps({"type":"status","message":"connected"}))
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
    data = await request.json()
    convo = data.get("conversation_id")
    message = data.get("message")

    if not convo or not message:
        return {"ok": False, "error": "missing fields"}

    # Echo it back as if the "agent" replied
    key = f"chat:{convo}"
    payload = {"type": "n8n_result", "data": {"result": f"Echo: {message}"}}
    await r.publish(key, json.dumps(payload))
    return {"ok": True}

@app.post("/webhooks/n8n/callback")
async def n8n_callback(request: Request, convo: str, secret: str):
    if secret != CALLBACK_SECRET:
        return {"ok": False, "error": "bad secret"}
    data = await request.json()
    key = f"chat:{convo}"
    await r.publish(key, json.dumps({"type":"n8n_result","data":data}))
    return {"ok": True}
