import type { Meta, StoryObj } from "@storybook/react";
import { TrendingRail } from "../organisms";
import type { TrendingPlace, TrendingTrace } from "../types";

const meta: Meta = { title: "Organisms/Trending" };
export default meta;
type Story = StoryObj;

const items: TrendingPlace[] = [
  {
    destination: "Kyoto",
    plans: 9,
    likes: 9,
    days: 4,
    year: 2026,
    places: ["Kiyomizu-dera", "Fushimi Inari Taisha", "Nijō Castle", "Arashiyama"],
    summary: "4-day plan · 4 sights",
  },
  {
    destination: "Hong Kong",
    plans: 7,
    likes: 7,
    days: 3,
    year: 2027,
    places: ["Tian Tan Buddha", "Hong Kong Disneyland", "Victoria Harbour"],
    summary: "3-day plan · 3 sights",
  },
  {
    destination: "Taipei",
    plans: 3,
    likes: 3,
    days: 2,
    year: 2026,
    places: ["Taipei 101", "Longshan Temple", "Shilin Night Market"],
    summary: "2-day plan · 3 sights",
  },
];

const trace: TrendingTrace = {
  note: "Aggregated from anonymous plans created in this workspace. Only destination, trip length, year and sight names are exposed.",
  filter: "",
  dropped: [],
};

export const Populated: Story = {
  render: () => (
    <div style={{ width: 400 }}>
      <TrendingRail items={items} trace={trace} onTry={() => undefined} onRefresh={() => undefined} />
    </div>
  ),
};

export const Empty: Story = {
  render: () => (
    <div style={{ width: 400 }}>
      <TrendingRail items={[]} trace={trace} />
    </div>
  ),
};

export const Loading: Story = {
  render: () => (
    <div style={{ width: 400 }}>
      <TrendingRail items={[]} loading />
    </div>
  ),
};
