import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { RootBoundary } from "./app/RootBoundary";
import { I18nProvider } from "./lib/i18n";
import { migrateLegacyStorage } from "./lib/storage";
import "./design-system/tokens.css";
import "./design-system/base.css";
import "./design-system/design-system.css";
import "./app/app.css";

// Carry `pathfinder30.*` values over to `pathfinder.*` before the first render,
// so a browser that predates the rename keeps its trips and session.
migrateLegacyStorage();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <RootBoundary>
        <App />
      </RootBoundary>
    </I18nProvider>
  </React.StrictMode>,
);
