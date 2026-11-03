import { useEffect, useState } from "react";
import {
  EvalDashboard,
  SavedTripsView,
  SettingsView,
  Banner,
  EmptyState,
  LoadingScreen,
} from "../design-system";
import {
  clearMemory,
  getConfig,
  getEvalDatasets,
  getEvalExport,
  getEvalRun,
  getEvalRuns,
  getInjections,
  getProfile,
  getSavedTrips,
  runEval,
  saveProfilePreferences,
} from "../lib/api";
import { failureText } from "../lib/failure";
import { useI18n } from "../lib/i18n";
import { clientToken as ensureClientToken } from "../lib/usePathfinder";
import type { ClientProfile, EvalDataset, EvalRun, EvalRunSummary } from "../design-system/types";

export function SavedPage() {
  const { t } = useI18n();
  const [trips, setTrips] = useState<Awaited<ReturnType<typeof getSavedTrips>>["trips"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getSavedTrips(ensureClientToken())
      .then((data) => setTrips(data.trips))
      .catch((cause) => setError(failureText(cause, t, "errors.savedTrips")))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingScreen label={t("saved.loading")} />;
  if (error) {
    return (
      <main className="pf-saved">
        <Banner>{error}</Banner>
      </main>
    );
  }
  return <SavedTripsView trips={trips} />;
}

export function EvaluationPage() {
  const { t } = useI18n();
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [latest, setLatest] = useState<EvalRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const [datasetData, runData] = await Promise.all([getEvalDatasets(), getEvalRuns()]);
      setDatasets(datasetData.datasets);
      setRuns(runData.runs);
      const preferred = runData.runs.find((run) => run.dataset_id === "hongkong") ?? runData.runs[0];
      if (preferred) {
        const detail = await getEvalRun(preferred.id);
        setLatest(detail.run);
      }
      setError(null);
    } catch (cause) {
      setError(failureText(cause, t, "errors.evalService"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleRun = async (dataset: string, tag: string) => {
    setRunning(true);
    setError(null);
    try {
      const data = await runEval(dataset, tag);
      setLatest(data.run);
      const runData = await getEvalRuns();
      setRuns(runData.runs);
    } catch (cause) {
      setError(failureText(cause, t, "errors.evalRun"));
    } finally {
      setRunning(false);
    }
  };

  const handleExport = async (runId: string) => {
    try {
      const report = await getEvalExport(runId);
      const url = URL.createObjectURL(new Blob([report], { type: "text/plain;charset=utf-8" }));
      window.open(url, "_blank", "noopener");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (cause) {
      setError(failureText(cause, t, "errors.evalOpen"));
    }
  };

  const handleSelectRun = async (runId: string) => {
    try {
      const detail = await getEvalRun(runId);
      setLatest(detail.run);
    } catch (cause) {
      setError(failureText(cause, t, "errors.evalOpen"));
    }
  };

  if (loading) return <LoadingScreen label={t("eval.loading")} />;
  if (!datasets.length && error) {
    return (
      <main className="pf-eval">
        <Banner>{error}</Banner>
      </main>
    );
  }
  if (!datasets.length) {
    return (
      <main className="pf-eval">
        <EmptyState title={t("eval.noDatasets")} body={t("eval.noDatasetsBody")} />
      </main>
    );
  }
  return (
    <EvalDashboard
      datasets={datasets}
      runs={runs}
      latest={latest}
      running={running}
      error={error}
      onRun={(dataset, tag) => void handleRun(dataset, tag)}
      onExport={(runId) => void handleExport(runId)}
      onSelectRun={(runId) => void handleSelectRun(runId)}
    />
  );
}

export function SettingsPage({ signedIn = false }: { signedIn?: boolean }) {
  const { t } = useI18n();
  const [config, setConfig] = useState<Parameters<typeof SettingsView>[0]["config"]>(null);
  const [injections, setInjections] = useState<{ category: string; severity: string; count: number }[]>([]);
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const clientToken = ensureClientToken();

  useEffect(() => {
    getConfig()
      .then((data) => setConfig(data as unknown as Parameters<typeof SettingsView>[0]["config"]))
      .catch((cause) => setError(failureText(cause, t, "errors.config")));
    // No built-in admin token: the admin request is only sent when one is stored.
    const adminToken = localStorage.getItem("pathfinder.admin") || "";
    if (adminToken) {
      getInjections(adminToken)
        .then((data) => setInjections(data.events))
        .catch(() => setInjections([]));
    } else {
      setInjections([]);
    }
    getProfile(clientToken)
      .then(setProfile)
      .catch(() => setProfile(null));
  }, [clientToken]);

  const handleSavePreferences = (preferences: string[]) => {
    setError(null);
    void saveProfilePreferences(clientToken, preferences)
      .then((data) => setProfile(data.profile))
      .catch((cause) => setError(failureText(cause, t, "errors.preferences")));
  };

  const handleClearMemory = () => {
    if (!window.confirm(t("settings.clearConfirm"))) return;
    setError(null);
    void clearMemory(clientToken)
      .then(() => getProfile(clientToken))
      .then(setProfile)
      .catch((cause) => setError(failureText(cause, t, "errors.memory")));
  };

  // A failure shows above the form instead of replacing it, so what was typed stays and Save can be pressed again.
  return (
    <SettingsView
      config={config}
      injections={injections}
      profile={profile}
      signedIn={signedIn}
      error={error}
      onSavePreferences={handleSavePreferences}
      onClearMemory={handleClearMemory}
    />
  );
}
