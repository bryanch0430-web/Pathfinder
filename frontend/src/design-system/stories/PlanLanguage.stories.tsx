import type { Meta, StoryObj } from "@storybook/react";
import { PlanLanguageBanner } from "../molecules";

/**
 * The banner on a plan viewed in Traditional Chinese: the AI-translation
 * disclaimer always, plus a notice when the English original is shown instead.
 * Switch the toolbar language to 繁 to see the Hong Kong wording.
 */
const meta: Meta<typeof PlanLanguageBanner> = {
  title: "Molecules/Plan language banner",
  component: PlanLanguageBanner,
  decorators: [
    (Story) => (
      <div className="pf-card" style={{ padding: 24, maxWidth: 720 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;
type Story = StoryObj<typeof PlanLanguageBanner>;

/** Translated plan: the disclaimer, with the fixed text in the interface language. */
export const Disclaimer: Story = { args: {} };

/** The disclaimer as the translation endpoint returned it. */
export const DisclaimerFromTranslation: Story = {
  args: {
    disclaimer:
      "此繁體中文版本由 AI 模型翻譯，可能有錯誤。英文版本為原文，請以英文版本核對日期、時間及價格等重要資料。",
  },
};

export const Translating: Story = { args: { notice: "translating" } };

/** The fact check failed: the English plan is shown, and the notice says why. */
export const FallbackNotice: Story = {
  args: { notice: "fallback", reason: "Day 2 time 14:00 became 16:00." },
};

/** No translator is configured on the server. */
export const UnavailableNotice: Story = { args: { notice: "unavailable" } };

export const LoadFailedNotice: Story = { args: { notice: "failed" } };
