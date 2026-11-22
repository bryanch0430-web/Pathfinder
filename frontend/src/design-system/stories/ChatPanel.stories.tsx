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

/** No trip is open (the server could not be reached): the box and the prompts are disabled, and the reason shows beside the trip. */
export const Disconnected: Story = {
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
        disabled
      />
    </div>
  ),
};

/**
 * Two files chosen for the next message (chips above the box, each removable), a sent message that names its file, and
 * what the turn read from the link and the files it was sent.
 */
export const WithAttachments: Story = {
  render: () => (
    <div style={{ padding: 24, maxWidth: 440 }}>
      <ChatPanel
        messages={[
          ...sampleMessages,
          {
            id: "u-attached",
            role: "user",
            content: "Plan a trip from what I attached.",
            created_at: new Date().toISOString(),
            meta: { attachments: ["kyoto-booking.pdf"] },
          },
        ]}
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
        intake={{
          sources: [
            { name: "kyoto-booking.pdf", kind: "pdf", status: "read", note: "" },
            { name: "https://example.com/kyoto-guide", kind: "url", status: "failed", note: "the page did not answer" },
          ],
          filled: ["destination", "start_date", "days"],
          places: ["Fushimi Inari", "Nishiki Market"],
        }}
        initialFiles={[
          new File([new Uint8Array(182_000)], "flight-confirmation.pdf", { type: "application/pdf" }),
          new File([new Uint8Array(1_450_000)], "ryokan-screenshot.png", { type: "image/png" }),
        ]}
      />
    </div>
  ),
};

/** A turn failed on an open trip: the message is an alert, once, with Retry. */
export const TurnFailed: Story = {
  render: () => (
    <div style={{ padding: 24, maxWidth: 440 }}>
      <ChatPanel
        messages={sampleMessages}
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
        error="The turn failed."
        onRetry={() => undefined}
      />
    </div>
  ),
};
