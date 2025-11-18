import React, { useEffect, useState } from "react";
import "./App.css";

export default function App() {
  const [agents, setAgents] = useState({});
  const [message, setMessage] = useState("");
  const [chat, setChat] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [conversationId, setConversationId] = useState(null);
  const [socket, setSocket] = useState(null);

  useEffect(() => {
    fetch("http://localhost:8000/agents")
      .then((r) => r.json())
      .then(setAgents);
  }, []);

  const startChat = async (agentId, input) => {
    const res = await fetch("http://localhost:8000/chat/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agentId, inputs: input }),
    });
    const data = await res.json();
    const convo = data.conversation_id;
    setConversationId(convo);
    const ws = new WebSocket(`ws://localhost:8000/ws/${convo}`);
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "n8n_result") {
        setChat((prev) => [...prev, { sender: "agent", text: msg.data.result }]);
      }
    };
    setSocket(ws);
  };

  const sendMessage = async () => {
    if (!message || !conversationId) return;
    setChat((prev) => [...prev, { sender: "user", text: message }]);
    await fetch("http://localhost:8000/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
//      body: JSON.stringify({ conversation_id: conversationId, message }),
      body: JSON.stringify({ conversation_id: conversationId, message, agent_id: selectedAgent.id}),
    });
    setMessage("");
  };

  return (
    <div>
      {/* HEADER */}
      <header className="header">
        <img src="/ibm-logo.png" alt="IBM Logo" />
        <h1>Consulting Sales AI</h1>
      </header>

      {/* WELCOME SECTION */}
      <section className="welcome">
        <h2>Welcome!</h2>
        <p>
          Meet your agents that will help you 10× your sales performance.
          You have two ways to use them:
        </p>
        <ul>
          <li>
            If you know the agent you want and what input it expects, just
            select it from the catalog.
          </li>
          <li>
            If you'd like an orchestrated experience instead, select the{" "}
            <strong>master orchestrator agent</strong> and let it guide you to
            the right agent as you interact with it.
          </li>
        </ul>
      </section>

      {/* MAIN CONTENT */}
      <main className="main">
        {/* AGENT CATALOG */}
	<div className="catalog">
	  <h2>Agent Catalog</h2>
	  {Object.keys(agents).map((stage) => (
	    <div key={stage}>
	      <h3>{stage}</h3>
	      <div className="agent-list">
		{agents[stage].map((agent) => (
		  <button
		    key={agent.id}
		    className={`agent-button ${selectedAgent?.id === agent.id ? "selected" : ""}`}
		    onClick={() => {
		      setSelectedAgent(agent);
		      setChat([]); // clear previous chat
		      startChat(agent.id, {});
		    }}
		  >
		    <div className="agent-entry">
		      <span className="agent-icon">🤖</span>
		      <div className="agent-info">
			<div className="agent-name">{agent.name}</div>
			<div className="agent-desc">{agent.description}</div>
		      </div>
		    </div>
		  </button>
		))}
	      </div>
	    </div>
	  ))}
	</div>


        {/* CHAT PANEL */}
        <div className="chat">
          <h2>
            {selectedAgent
              ? `Chat with ${selectedAgent.name}`
              : "Select an agent from the catalog to start your work"}
          </h2>

          <div className="chat-messages">
            {chat.map((c, i) => (
              <div
                key={i}
                className={`chat-bubble ${c.sender === "user" ? "user" : "agent"}`}
              >
                {c.sender === "agent" ? `Agent: ${c.text}` : c.text}
              </div>
            ))}
          </div>

          {selectedAgent && (
            <div className="chat-input">
              <input
                placeholder="Type your message..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
              />
              <button onClick={sendMessage}>Send</button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
