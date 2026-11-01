import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { AppHeader, ChatPanel, MapPanel, TripPanel, TripWorkspace } from "../organisms";
import { PlannerTemplate } from "../templates";
import { sampleContext, sampleMessages, samplePlan } from "../fixtures";
import type { PlanItem, TripContext } from "../types";

const meta: Meta = { title: "Templates/Planner" };
export default meta;
type Story = StoryObj;

export const ThreeColumns: Story = {
  render: () => {
    const [tab, setTab] = useState<"plan" | "saved" | "evaluation" | "settings">("plan");
    const [locale, setLocale] = useState("en");
    const [context, setContext] = useState<Partial<TripContext>>(sampleContext);
    const [selected, setSelected] = useState<PlanItem | null>(null);
    return (
      <PlannerTemplate
        header={<AppHeader activeTab={tab} onTabChange={setTab} locale={locale} onLocaleChange={setLocale} />}
        left={
          <div className="pf-stack pf-stack--md">
            <section className="pf-card" style={{ padding: 16 }}>
              <TripPanel
                context={context}
                plan={samplePlan}
                onChange={(patch) => setContext((current) => ({ ...current, ...patch }))}
                onSave={() => undefined}
                onGenerate={() => undefined}
              />
            </section>
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
              scope={selected ? selected.name : null}
              onClearScope={() => setSelected(null)}
              error={null}
            />
          </div>
        }
        centre={
          <TripWorkspace
            plan={samplePlan}
            versions={[
              { version: 2, change_summary: "Swapped the hotel", created_at: "2026-10-30T08:00:00Z" },
              { version: 1, change_summary: "Initial plan", created_at: "2026-10-30T07:30:00Z" },
            ]}
            activeVersion={2}
            onVersionChange={() => undefined}
            selectedItemId={selected?.id ?? null}
            onSelectItem={setSelected}
            onUseful={() => undefined}
            onNotUseful={() => undefined}
            feedbackStars={null}
            feedbackVerdict={null}
          />
        }
        right={
          <MapPanel
            plan={samplePlan}
            activeDay={samplePlan.days[0].date}
            onActiveDay={() => undefined}
            selectedItemId={selected?.id ?? null}
            onSelectItem={setSelected}
            onOptimize={() => undefined}
          />
        }
      />
    );
  },
};

export const Phone: Story = {
  ...ThreeColumns,
  parameters: { viewport: { defaultViewport: "phone" } },
};
