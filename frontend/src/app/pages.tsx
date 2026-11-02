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
  evalExportUrl,
  clearMemory,
  getConfig,
  getEvalDatasets,
  getEvalRun,
  getEvalRuns,
  getInjections,
  getProfile,
  getSavedTrips,
  runEval,
  saveProfilePreferences,
} from "../lib/api";
import type { ClientProfile, EvalDataset, EvalRun, EvalRunSummary } from "../design-system/types";

export function SavedPage() {
  const [trips, setTrips] = useState<Awaited<ReturnType<typeof getSavedTrips>>["trips"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getSavedTrips()
      .then((data) => setTrips(data.trips))
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Could not load saved trips."))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingScreen label="Loading saved trips" />;
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
      setError(cause instanceof Error ? cause.message : "The evaluation service is unavailable.");
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
      setError(cause instanceof Error ? cause.message : "The evaluation run failed.");
    } finally {
      setRunning(false);
    }
  };

  const handleSelectRun = async (runId: string) => {
    try {
      const detail = await getEvalRun(runId);
      setLatest(detail.run);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The run could not be opened.");
    }
  };

  if (loading) return <LoadingScreen label="Loading evaluation" />;
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
        <EmptyState title="No evaluation datasets" body="The backend seeds routing, injection, Hong Kong, Japan and TravelPlanner fixtures on first start." />
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
      onExport={(runId) => window.open(evalExportUrl(runId), "_blank", "noopener")}
      onSelectRun={(runId) => void handleSelectRun(runId)}
    />
  );
}

export function SettingsPage({ signedIn = false }: { signedIn?: boolean }) {
  const [config, setConfig] = useState<Parameters<typeof SettingsView>[0]["config"]>(null);
  const [injections, setInjections] = useState<{ category: string; severity: string; count: number }[]>([]);
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const clientToken = localStorage.getItem("pathfinder.client") || "anonymous";

  useEffect(() => {
    getConfig()
      .then((data) => setConfig(data as unknown as Parameters<typeof SettingsView>[0]["config"]))
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Configuration is unavailable."));
    const token = localStorage.getItem("pathfinder.admin") || "local-admin";
    getInjections(token)
      .then((data) => setInjections(data.events))
      .catch(() => setInjections([]));
    getProfile(clientToken)
      .then(setProfile)
      .catch(() => setProfile(null));
  }, [clientToken]);

  const handleSavePreferences = (preferences: string[]) => {
    void saveProfilePreferences(clientToken, preferences)
      .then((data) => setProfile(data.profile))
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Preferences could not be saved."));
  };

  const handleClearMemory = () => {
    if (!window.confirm("Clear saved trips and remembered preferences? Your current itinerary and chat stay.")) return;
    void clearMemory()
      .then(() => getProfile(clientToken))
      .then(setProfile)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Memory could not be cleared."));
  };

  return (
    <>
      {error ? (
        <main className="pf-settings">
          <Banner>{error}</Banner>
        </main>
      ) : (
        <SettingsView
          config={config}
          injections={injections}
          profile={profile}
          signedIn={signedIn}
          onSavePreferences={handleSavePreferences}
          onClearMemory={handleClearMemory}
        />
      )}
    </>
  );
}
