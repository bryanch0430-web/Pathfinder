import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ChatBubble, ClarifyCard } from "../molecules";
import { sampleClarify, sampleMessages } from "../fixtures";
import { Badge } from "../atoms";

const meta: Meta = { title: "Molecules/Chat" };
export default meta;
type Story = StoryObj;

export const Bubbles: Story = {
  render: () => (
    <div className="pf-stack pf-stack--md" style={{ padding: 24, maxWidth: 620 }}>
      {sampleMessages.map((message) => (
        <ChatBubble
          key={message.id}
          role={message.role === "user" ? "user" : "assistant"}
          intent={message.intent ?? undefined}
          time="09:41"
        >
          {message.content}
        </ChatBubble>
      ))}
      <ChatBubble role="assistant" intent="ask" time="09:42" meta={<Badge tone="info">plan · tool</Badge>}>
        The current plan contains weather data for day 2: overcast, 16° / 10°, 65% rain.
      </ChatBubble>
    </div>
  ),
};

export const Clarification: Story = {
  render: () => {
    const [values, setValues] = useState<Record<string, string>>({});
    return (
      <div style={{ padding: 24, maxWidth: 560 }}>
        <ClarifyCard
          payload={sampleClarify}
          values={values}
          onChange={(slot, value) => setValues((current) => ({ ...current, [slot]: value }))}
          onSubmit={() => undefined}
        />
      </div>
    );
  },
};
