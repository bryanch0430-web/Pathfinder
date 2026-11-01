import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ChatPanel } from "../organisms";
import { sampleClarify, sampleMessages } from "../fixtures";
import type { AgentState, ChatMessage } from "../types";

const meta: Meta = { title: "Organisms/Chat panel" };
export default meta;
type Story = StoryObj;

const agents: Record<string, AgentState> = {
  attraction: { agent: "attraction", status: "ok", summary: "8 attractions" },
  hotel: { agent: "hotel", status: "ok", summary: "4 candidates" },
  weather: { agent: "weather", status: "running" },
  ticket: { agent: "ticket", status: "unavailable" },
};

export const Playground: Story = {
  render: () => {
    const [messages, setMessages] = useState<ChatMessage[]>(sampleMessages);
    const [clarify, setClarify] = useState<import("../types").ClarifyPayload | null>(sampleClarify);
    const [values, setValues] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState(false);
    return (
      <div style={{ padding: 24, maxWidth: 440 }}>
        <ChatPanel
          messages={messages}
          busy={busy}
          agents={agents}
          progress={64}
          progressMessage="Composing the itinerary"
          clarify={clarify}
          clarifyValues={values}
          onClarifyChange={(slot, value) => setValues((current) => ({ ...current, [slot]: value }))}
          onClarifySubmit={() => {
            setClarify(null);
            setBusy(true);
            setMessages((current) => [
              ...current,
              { id: `u${current.length}`, role: "user", content: Object.entries(values).map(([key, value]) => `${key}: ${value}`).join(", "), created_at: new Date().toISOString() },
            ]);
          }}
          onSend={(message) => {
            setBusy(true);
            setMessages((current) => [...current, { id: `u${current.length}`, role: "user", content: message, created_at: new Date().toISOString() }]);
            window.setTimeout(() => {
              setBusy(false);
              setMessages((current) => [
                ...current,
                { id: `a${current.length}`, role: "assistant", content: "This is a storybook reply. The live app streams the real answer here.", created_at: new Date().toISOString(), intent: "ask" },
              ]);
            }, 700);
          }}
          scope="Day 2 · Fushimi Inari"
          onClearScope={() => undefined}
          error={null}
        />
      </div>
    );
  },
};

export const Empty: Story = {
  render: () => (
    <div style={{ padding: 24, maxWidth: 440 }}>
      <ChatPanel
        messages={[]}
        busy={false}
        agents={{}}
        progress={0}
        progressMessage=""
        clarify={null}
        clarifyValues={{}}
        onClarifyChange={() => undefined}
        onClarifySubmit={() => undefined}
        onSend={() => undefined}
        scope={null}
        onClearScope={() => undefined}
        error={null}
      />
    </div>
  ),
};
