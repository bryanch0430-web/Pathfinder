import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, EmptyState } from "../design-system";
import { useI18n } from "../lib/i18n";

function CrashNotice() {
  const { t } = useI18n();
  return (
    <div role="alert">
      <EmptyState
        icon="alert"
        title={t("errors.crashed")}
        action={<Button onClick={() => window.location.reload()}>{t("common.retry")}</Button>}
      />
    </div>
  );
}

/** Catches what a render throws, so the page says so and offers a reload instead of going blank. */
export class RootBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  render() {
    return this.state.failed ? <CrashNotice /> : this.props.children;
  }
}
