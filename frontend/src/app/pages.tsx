import { useEffect, useState } from "react";
import { AdminTune } from "./adminTune";
import {
  EvalDashboard,
  SavedTripsView,
  SettingsView,
  Banner,
  EmptyState,
  LoadingScreen,
} from "../design-system";
import {
  adminHeader,
  clearMemory,
  getConfig,
  resetRuntime,
  saveRuntime,
  getEvalDatasets,
  getEvalExport,
  getEvalRun,
  getEvalRuns,
  getEvalSuiteCases,
  getInjections,
  getLlmKeys,
  getLlmProviders,
  chooseLlmKey,
  deleteLlmKey,
  patchLlmKey,
  retestLlmKey,
  saveLlmKey,
  testLlmKey,
  type KeyScope,
  type LlmKey,
  type LlmProvider,
  type LlmStatus,
  getProfile,
  getSavedTrips,
  runEval,
  saveProfilePreferences,
} from "../lib/api";
import { showsAdmin } from "../lib/access";
import { describeFailure, failureOf, type Failure } from "../lib/failure";
import { useI18n, type MessageKey } from "../lib/i18n";
import { loadLocalAdmin, useLocalAdmin } from "../lib/localAdmin";
import { readStoredValue } from "../lib/storage";
import { clientToken as ensureClientToken } from "../lib/usePathfinder";
import type { ClientProfile, EvalDataset, EvalPreview, EvalRun, EvalRunSummary } from "../design-system/types";

export function SavedPage() {
  const { t } = useI18n();
  const [trips, setTrips] = useState<Awaited<ReturnType<typeof getSavedTrips>>["trips"]>([]);
  // Kept as a message key, so that a language switch translates it too.
  const [error, setError] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getSavedTrips(ensureClientToken())
      .then((data) => setTrips(data.trips))
      .catch((cause) => setError(failureOf(cause, "errors.savedTrips")))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingScreen label={t("saved.loading")} />;
  if (error) {
    return (
      <main className="pf-saved">
        <Banner>{describeFailure(error, t)}</Banner>
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
  const [opened, setOpened] = useState<Record<string, EvalRun>>({});
  const [previews, setPreviews] = useState<Record<string, EvalPreview[]>>({});
  const [detail, setDetail] = useState<EvalRun | null>(null);
  const [running, setRunning] = useState(false);
  // Kept as a message key, so that a language switch translates it too.
  const [error, setError] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);

  // On the evaluation routes a 401 means the admin token, not the traveller's sign-in.
  const failure = (cause: unknown, key: MessageKey) => failureOf(cause, key, "eval.adminRequired");

  const load = async () => {
    // Every evaluation route needs the admin token, so without one a request can only be refused, unless this is
    // the owner's own machine in local admin mode.
    if (!adminHeader()["X-Admin-Token"] && !(await loadLocalAdmin())) {
      setError("eval.adminRequired");
      setLoading(false);
      return;
    }
    try {
      const [datasetData, runData] = await Promise.all([getEvalDatasets(), getEvalRuns()]);
      setDatasets(datasetData.datasets);
      setRuns(runData.runs);
      const newest = new Map<string, string>();
      for (const run of runData.runs) {
        if (!newest.has(run.dataset_id)) newest.set(run.dataset_id, run.id);
      }
      const [scored, defined] = await Promise.all([
        Promise.all(
          [...newest.entries()].map(async ([datasetId, runId]) => {
            const loaded = await getEvalRun(runId);
            return [datasetId, loaded.run] as const;
          }),
        ),
        Promise.all(
          datasetData.datasets
            .filter((dataset) => !newest.has(dataset.id))
            .map(async (dataset) => {
              const loaded = await getEvalSuiteCases(dataset.id);
              return [dataset.id, loaded.cases] as const;
            }),
        ),
      ]);
      const bySuite = Object.fromEntries(scored);
      setOpened(bySuite);
      setPreviews(Object.fromEntries(defined));
      setLatest(scored[0]?.[1] ?? null);
      setError(null);
    } catch (cause) {
      setError(failure(cause, "errors.evalService"));
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
      setDetail(data.run);
      setOpened((current) => ({ ...current, [data.run.dataset_id]: data.run }));
      setPreviews((current) => {
        const next = { ...current };
        delete next[data.run.dataset_id];
        return next;
      });
      const runData = await getEvalRuns();
      setRuns(runData.runs);
    } catch (cause) {
      setError(failure(cause, "errors.evalRun"));
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
      setError(failure(cause, "errors.evalOpen"));
    }
  };

  const handleSelectRun = async (runId: string) => {
    try {
      const loaded = await getEvalRun(runId);
      setLatest(loaded.run);
      setDetail(loaded.run);
    } catch (cause) {
      setError(failure(cause, "errors.evalOpen"));
    }
  };

  if (loading) return <LoadingScreen label={t("eval.loading")} />;
  if (!datasets.length && error) {
    return (
      <main className="pf-eval">
        <Banner>{describeFailure(error, t)}</Banner>
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
      error={error === null ? null : describeFailure(error, t)}
      onRun={(dataset, tag) => void handleRun(dataset, tag)}
      onExport={(runId) => void handleExport(runId)}
      onSelectRun={(runId) => void handleSelectRun(runId)}
      opened={opened}
      previews={previews}
      detail={detail}
    />
  );
}

type KeysCardData = NonNullable<Parameters<typeof SettingsView>[0]["modelKeys"]>;

/**
 * One "Model & API keys" card's data and handlers: the traveller's own (`scope` undefined) or the operator's
 * (`"server"`). Every change reloads the list and status, so the card always shows what the server holds.
 * A key typed into the card goes to the request body and nowhere else: it is not kept here or in storage.
 */
function useModelKeys(scope: KeyScope, providers: LlmProvider[], enabled = true): KeysCardData {
  const [keys, setKeys] = useState<LlmKey[]>([]);
  const [status, setStatus] = useState<LlmStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const { t } = useI18n();

  const reload = () =>
    getLlmKeys(scope)
      .then((data) => {
        setKeys(data.keys);
        setStatus(data.status);
      })
      .catch((cause) => setFailure(failureOf(cause, "errors.keys", "errors.keyAction")));
  useEffect(() => {
    if (enabled) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, enabled]);

  /** Runs one request: busy while it runs, a failure shown on the card, `null` when it failed. */
  const run = async <T,>(call: () => Promise<T>, refresh: boolean): Promise<T | null> => {
    setBusy(true);
    setFailure(null);
    try {
      const value = await call();
      if (refresh) await reload();
      return value;
    } catch (cause) {
      setFailure(failureOf(cause, "errors.keyAction", "errors.keyAction"));
      return null;
    } finally {
      setBusy(false);
    }
  };

  return {
    providers,
    keys,
    status,
    busy,
    error: failure === null ? null : describeFailure(failure, t),
    onTest: (body) => run(() => testLlmKey(body, scope), false),
    onSave: (body) => run(() => saveLlmKey(body, scope), true).then((saved) => (saved ? { warning: saved.warning } : null)),
    onRetest: (id) => run(() => retestLlmKey(id, scope), true).then((done) => done?.result ?? null),
    onPatch: async (id, body) => void (await run(() => patchLlmKey(id, body, scope), true)),
    onChoose: async (id) => void (await run(() => chooseLlmKey(id, scope), true)),
    onDelete: async (id) => void (await run(() => deleteLlmKey(id, scope), true)),
  };
}

export function SettingsPage({ signedIn = false }: { signedIn?: boolean }) {
  const { t } = useI18n();
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const ownKeys = useModelKeys(undefined, providers);
  const localAdmin = useLocalAdmin();
  // The server card is for the operator: it shows only where an admin token is stored in this browser, or in
  // local admin mode on the owner's own machine.
  const hasAdminToken = showsAdmin(localAdmin, readStoredValue("admin"));
  const serverKeys = useModelKeys("server", providers, hasAdminToken);
  useEffect(() => {
    getLlmProviders()
      .then((data) => setProviders(data.providers))
      .catch(() => setProviders([]));
  }, []);
  const [config, setConfig] = useState<Parameters<typeof SettingsView>[0]["config"]>(null);
  const [injections, setInjections] = useState<{ category: string; severity: string; count: number }[]>([]);
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  // Kept as a message key, so that a language switch translates it too.
  const [error, setError] = useState<Failure | null>(null);
  const clientToken = ensureClientToken();

  useEffect(() => {
    getConfig()
      .then((data) => setConfig(data as unknown as Parameters<typeof SettingsView>[0]["config"]))
      .catch((cause) => setError(failureOf(cause, "errors.config")));
    // No built-in admin token: the admin request is only sent when one is stored, or in local admin mode.
    const adminToken = readStoredValue("admin") || "";
    if (showsAdmin(localAdmin, adminToken)) {
      getInjections(adminToken)
        .then((data) => setInjections(data.events))
        .catch(() => setInjections([]));
    } else {
      setInjections([]);
    }
    getProfile(clientToken)
      .then(setProfile)
      .catch(() => setProfile(null));
  }, [clientToken, localAdmin]);

  const handleSavePreferences = (preferences: string[]) => {
    setError(null);
    void saveProfilePreferences(clientToken, preferences)
      .then((data) => setProfile(data.profile))
      .catch((cause) => setError(failureOf(cause, "errors.preferences")));
  };

  const handleClearMemory = () => {
    if (!window.confirm(t("settings.clearConfirm"))) return;
    setError(null);
    void clearMemory(clientToken)
      .then(() => getProfile(clientToken))
      .then(setProfile)
      .catch((cause) => setError(failureOf(cause, "errors.memory")));
  };

  // A failure shows above the form instead of replacing it, so what was typed stays and Save can be pressed again.
  return (
    <SettingsView
      config={config}
      injections={injections}
      profile={profile}
      signedIn={signedIn}
      error={error === null ? null : describeFailure(error, t)}
      onSavePreferences={handleSavePreferences}
      onClearMemory={handleClearMemory}
      modelKeys={ownKeys}
      serverKeys={hasAdminToken ? serverKeys : undefined}
      providers={providers}
      admin={hasAdminToken ? (show) => <AdminTune show={show} /> : undefined}
      canEditPolicy={hasAdminToken}
      onSavePolicy={async (body) => {
        await saveRuntime(body);
        setConfig((await getConfig()) as unknown as Parameters<typeof SettingsView>[0]["config"]);
      }}
      onResetPolicy={async () => {
        await resetRuntime();
        setConfig((await getConfig()) as unknown as Parameters<typeof SettingsView>[0]["config"]);
      }}
      onSaveGate={async (gate) => {
        await saveRuntime({ gate });
        setConfig((await getConfig()) as unknown as Parameters<typeof SettingsView>[0]["config"]);
      }}
      onSaveOrchestrator={async (body) => {
        await saveRuntime(body);
        setConfig((await getConfig()) as unknown as Parameters<typeof SettingsView>[0]["config"]);
      }}
    />
  );
}
