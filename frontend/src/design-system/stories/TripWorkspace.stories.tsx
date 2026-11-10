import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { TripWorkspace } from "../organisms";
import { optionsPlan, samplePlan } from "../fixtures";
import type { PlanItem, RoomType, RunTrace, TripPlan } from "../types";

const meta: Meta = { title: "Organisms/Trip workspace" };
export default meta;
type Story = StoryObj;

const versions = [
  { version: 2, change_summary: "Swapped the hotel", created_at: "2026-10-30T08:00:00Z" },
  { version: 1, change_summary: "Initial plan", created_at: "2026-10-30T07:30:00Z" },
];

const trace: RunTrace = {
  request_id: "req_8b2b12031486",
  spans: [
    { name: "understand", duration_ms: 18, status: "ok" },
    { name: "route", duration_ms: 240, status: "ok" },
    { name: "agent.attraction", duration_ms: 1280, status: "ok" },
    { name: "agent.hotel", duration_ms: 1120, status: "ok" },
    { name: "agent.weather", duration_ms: 86, status: "ok" },
    { name: "agent.ticket", duration_ms: 960, status: "ok" },
    { name: "llm.planner", duration_ms: 2140, status: "ok" },
  ],
  generations: [{ purpose: "planner", model: "grok-4.20-0309-non-reasoning", prompt_tokens: 4200, completion_tokens: 320, latency_ms: 2140 }],
  tool_calls: 7,
};

export const Playground: Story = {
  render: () => {
    const [selected, setSelected] = useState<PlanItem | null>(null);
    return (
      <div style={{ padding: 24, maxWidth: 760 }}>
        <TripWorkspace
          plan={samplePlan}
          versions={versions}
          activeVersion={2}
          onVersionChange={() => undefined}
          selectedItemId={selected?.id ?? null}
          onSelectItem={setSelected}
          onUseful={() => undefined}
          onNotUseful={() => undefined}
          feedbackStars={null}
          feedbackVerdict={null}
          trace={trace}
        />
      </div>
    );
  },
};

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

/** Meal slots with source facts and backups, the hotel comparison and the flight choice. "Station Inn Kyoto" fails to show the error state. */
export const WithOptions: Story = {
  render: () => {
    const [plan, setPlan] = useState<TripPlan>(optionsPlan);
    const [selected, setSelected] = useState<PlanItem | null>(null);
    const choose = async (kind: "item" | "hotel" | "flight", targetId: string | null, optionId: string) => {
      await wait(700);
      if (optionId === "hotel_h3") throw new Error("This stay is no longer offered by the source.");
      setPlan((current) => {
        if (kind === "hotel") {
          const stay = current.hotel_options?.find((option) => option.id === optionId);
          return stay ? { ...current, hotels: [stay] } : current;
        }
        if (kind === "flight") {
          return { ...current, tickets: current.tickets.map((ticket) => ({ ...ticket, selected: ticket.id === optionId })) };
        }
        return {
          ...current,
          days: current.days.map((day) => ({
            ...day,
            items: day.items.map((entry) => {
              if (entry.id !== targetId) return entry;
              const option = entry.alternatives?.find((alternative) => alternative.id === optionId);
              return option
                ? { ...entry, name: option.name, rating: option.rating, review_count: option.review_count, price_level: option.price_level, cuisine: option.cuisine, reservable: option.reservable }
                : entry;
            }),
          })),
        };
      });
    };
    const rooms = async (hotelId: string): Promise<RoomType[]> => {
      await wait(500);
      if (hotelId !== "hotel_h1") return [];
      return [
        { name: "Moderate Double", price_per_night: { amount: 17230, currency: "JPY" }, source: "Booking.com", link: "https://example.com/room" },
        { name: "Twin Room", price_per_night: null, source: "Hotel website", link: "" },
      ];
    };
    return (
      <div style={{ padding: 24, maxWidth: 760 }}>
        <TripWorkspace
          plan={plan}
          versions={versions}
          activeVersion={2}
          onVersionChange={() => undefined}
          selectedItemId={selected?.id ?? null}
          onSelectItem={setSelected}
          onUseful={() => undefined}
          onNotUseful={() => undefined}
          feedbackStars={null}
          feedbackVerdict={null}
          onChooseOption={choose}
          onLoadHotelRooms={rooms}
        />
      </div>
    );
  },
};
