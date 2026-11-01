import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { AppHeader } from "../organisms";

const meta: Meta = { title: "Organisms/App header" };
export default meta;
type Story = StoryObj;

export const Playground: Story = {
  render: () => {
    const [tab, setTab] = useState<"plan" | "saved" | "evaluation" | "settings">("plan");
    const [locale, setLocale] = useState("en");
    return (
      <div className="pf-app">
        <AppHeader activeTab={tab} onTabChange={setTab} locale={locale} onLocaleChange={setLocale} />
        <div style={{ padding: 32 }}>
          <p className="pf-text" data-tone="secondary">Active tab: {tab}</p>
        </div>
      </div>
    );
  },
};
