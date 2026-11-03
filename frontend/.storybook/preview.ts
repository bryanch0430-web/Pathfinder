import type { Preview } from "@storybook/react";
import { createElement } from "react";
import { I18nProvider, type Locale } from "../src/lib/i18n";
import "../src/design-system/tokens.css";
import "../src/design-system/base.css";
import "../src/design-system/design-system.css";

const preview: Preview = {
  parameters: {
    layout: "fullscreen",
    backgrounds: {
      default: "layout",
      values: [
        { name: "layout", value: "#F4F2EE" },
        { name: "surface", value: "#FFFFFF" },
      ],
    },
    viewport: {
      viewports: {
        phone: { name: "Phone · 390", styles: { width: "390px", height: "844px" } },
        tablet: { name: "Tablet · 900", styles: { width: "900px", height: "1000px" } },
        desktop: { name: "Desktop · 1440", styles: { width: "1440px", height: "1000px" } },
      },
    },
    options: {
      storySort: {
        order: ["Foundations", "Atoms", "Elements", "Molecules", "Organisms", "Templates"],
      },
    },
  },
  globalTypes: {
    locale: {
      description: "Interface language",
      toolbar: {
        title: "Language",
        icon: "globe",
        items: [
          { value: "en", title: "EN" },
          { value: "zh-Hans", title: "简" },
          { value: "zh-Hant", title: "繁" },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: { locale: "en" },
  decorators: [
    // English needs no provider: every component falls back to it on its own.
    (Story, context) => {
      const locale = context.globals.locale as Locale | undefined;
      if (!locale || locale === "en") return createElement(Story);
      return createElement(I18nProvider, { locale }, createElement(Story));
    },
  ],
};

export default preview;
