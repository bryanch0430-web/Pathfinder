import type { Preview } from "@storybook/react";
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
};

export default preview;
