import { useEffect, useRef, useState } from "react";

const STAGES = ["targeting", "origination", "progression", "growth"];
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function App() {
  const [agents, setAgents] = useState({});
  const [selected, setSelected] = useState(null);
  const [chat, setChat] = useState([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const wsRef = useRef(null);

  // Fetch agents for catalog
  useEffect(() => {
    fetch(`${API_BASE}/agents`).then((r) => r.json()).then(setAgents);
  }, []);

  // Start a chat session for a chosen agent
  async function startRun(agent) {
    const r = await fetch(`${API_BASE}/chat/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agent.id, inputs: {} }),
    });
    const { conversation_id } = await r.json();

    // Open websocket for this conversation
    const wsUrl = `${API_BASE.replace("http", "ws")}/ws/${conversation_id}`;
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      setChat((prev) => [...prev, msg]);
    };
    wsRef.current = ws;
    setConversationId(conversation_id);
    setSelected(agent);
    setChat([]); // reset chat when switching agents
  }

  // Send a user message → backend → echo back via WebSocket
  async function sendMessage() {
    if (!input || !conversationId) return;
    setChat((prev) => [...prev, { type: "user", message: input }]);

    await fetch(`${API_BASE}/chat/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId, message: input }),
    });

    setInput("");
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 2fr",
        gap: "1rem",
        padding: "1rem",
      }}
    >
      {/* Left column — catalog */}
      <div>
        <h2>AI Agents by Sales Stage</h2>
        {STAGES.map((stage) => (
          <div key={stage}>
            <h3 style={{ textTransform: "capitalize" }}>{stage}</h3>
            <ul>
              {(agents[stage] || []).map((a) => (
                <li key={a.id} style={{ marginBottom: "0.5rem" }}>
                  <button onClick={() => startRun(a)}>{a.name}</button>
                  <div style={{ fontSize: "0.9em" }}>{a.description}</div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Right column — chat UI */}
      <div>
        <h2>Chat</h2>

        {/* Chat window */}
        <div
          style={{
            background: "#111",
            color: "#0f0",
            padding: "1rem",
            height: "50vh",
            overflow: "auto",
            borderRadius: "8px",
            marginBottom: "1rem",
          }}
        >
          {chat.map((m, i) => (
            <div key={i}>
              {m.type === "user"
                ? `You: ${m.message}`
                : `Agent: ${JSON.stringify(m.data || m.message || m)}`}
            </div>
          ))}
        </div>

        {/* Input box */}
        {selected && (
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type a message..."
              style={{ flex: 1, padding: "0.5rem", borderRadius: "4px" }}
            />
            <button onClick={sendMessage} style={{ padding: "0.5rem 1rem" }}>
              Send
            </button>
          </div>
        )}

        {/* Debug console */}
        <div
          style={{
            background: "#000",
            color: "#0f0",
            padding: "1rem",
            marginTop: "1rem",
            height: "15vh",
            overflow: "auto",
            fontSize: "0.8em",
          }}
        >
          Debug console output...
        </div>

        {selected && (
          <div style={{ marginTop: "0.5rem" }}>
            Running: <b>{selected.name}</b>
          </div>
        )}
      </div>
    </div>
  );
}
