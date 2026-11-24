import { useState } from "react";
import { Button, Input, Select, Text } from "./atoms";
import { Field } from "./molecules";

type ModelsConfig = {
  default: string;
  temperature?: number;
  reasoning?: string;
  purposes: Record<string, string>;
  temperatures?: Record<string, number>;
  reasoning_by_purpose?: Record<string, string>;
  max_tokens_by_purpose?: Record<string, number>;
  effective_max_tokens?: Record<string, number>;
  verifier?: {
    model: string;
    base_url: string;
    key_set: boolean;
    temperature?: number;
    max_tokens?: number;
  };
};

type JevConfig = {
  model: string;
  url: string;
  key_set: boolean;
  key_tail: string;
};

type OrchestratorConfig = {
  mode: "analyst" | "jev";
  jev: JevConfig;
};

const PARTS: { key: string; label: string; hint: string }[] = [
  { key: "understand", label: "Analyst", hint: "Reads the message, routes the turn and extracts the intent and slots." },
  { key: "plan", label: "Planner", hint: "Chooses the stops, order and timings for a new plan." },
  { key: "edit", label: "Editor", hint: "Turns a change request into typed edit operations." },
  { key: "verify", label: "Verifier", hint: "Audits the traveller’s hard constraints against the plan and the sources." },
  { key: "ask", label: "Ask", hint: "Answers a question about the current plan." },
  { key: "memory", label: "Memory", hint: "Extracts durable preferences after a finalised trip." },
  { key: "intake", label: "Intake", hint: "Reads pasted text, documents and images into trip details." },
  { key: "translate", label: "Translation", hint: "Writes the plan in the traveller’s interface language." },
  { key: "search_query", label: "Search query", hint: "Turns a gap in the plan into a short web-search query." },
];

/**
 * Model routing for the parts of one turn. All parts share the active provider
 * key (BYOK included); only the model name can differ here. The model card above
 * chooses that key and the default model.
 */
export function ModelsSettings({
  models,
  orchestrator,
  canEdit,
  onSave,
}: {
  models: ModelsConfig;
  orchestrator: OrchestratorConfig;
  canEdit: boolean;
  onSave: (body: Record<string, unknown>) => Promise<void> | void;
}) {
  const [purposes, setPurposes] = useState<Record<string, string>>({ ...models.purposes });
  const [temperatures, setTemperatures] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(models.temperatures ?? {}).map(([key, value]) => [key, String(value)])),
  );
  const [reasoning, setReasoning] = useState<Record<string, string>>({ ...(models.reasoning_by_purpose ?? {}) });
  const [maxTokens, setMaxTokens] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(models.max_tokens_by_purpose ?? {}).map(([key, value]) => [key, String(value)])),
  );
  const [mode, setMode] = useState<"analyst" | "jev">(orchestrator.mode === "jev" ? "jev" : "analyst");
  const [jevModel, setJevModel] = useState(orchestrator.jev.model);
  const [jevUrl, setJevUrl] = useState(orchestrator.jev.url);
  const [jevKey, setJevKey] = useState("");
  const [open, setOpen] = useState(true);

  return (
    <div className="pf-stack pf-stack--md">
      <div className="pf-card pf-eval__panel">
        <div className="pf-row pf-row--between">
          <div>
            <Text size="sm" weight="semibold">Models by part</Text>
            <Text size="xs" tone="tertiary">
              Each part can use its own model; leave a field blank to use the default model
              (<span className="pf-mono">{models.default || "not set"}</span>). Each part can also set
              its own temperature, reasoning effort and reply cap. All parts use the active provider key,
              including a BYOK key, except the Constraint verifier when its own endpoint/key is
              configured by the operator.
            </Text>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setOpen((value) => !value)}>
            {open ? "Hide" : "Show parts"}
          </Button>
        </div>
        {open ? (
          <div className="pf-form-grid">
            {PARTS.map((part) => (
              <Field key={part.key} label={part.label} hint={part.hint} wide>
                <div className="pf-row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Input
                    style={{ flex: "1 1 240px" }}
                    value={purposes[part.key] ?? ""}
                    placeholder={part.key === "verify" && models.verifier?.model ? models.verifier.model : "Default model"}
                    disabled={!canEdit}
                    onChange={(event) => setPurposes((current) => ({ ...current, [part.key]: event.target.value }))}
                  />
                  <Input
                    style={{ width: 120 }}
                    value={temperatures[part.key] ?? ""}
                    placeholder={`Temp ${models.temperature ?? 0.2}`}
                    inputMode="decimal"
                    disabled={!canEdit}
                    onChange={(event) => setTemperatures((current) => ({ ...current, [part.key]: event.target.value }))}
                  />
                  <Input
                    style={{ width: 130 }}
                    value={maxTokens[part.key] ?? ""}
                    placeholder={models.effective_max_tokens?.[part.key] ? `Effective ${models.effective_max_tokens[part.key].toLocaleString()}` : "Cap auto"}
                    inputMode="numeric"
                    disabled={!canEdit}
                    onChange={(event) => setMaxTokens((current) => ({ ...current, [part.key]: event.target.value }))}
                  />
                  <Select
                    style={{ width: 170 }}
                    value={reasoning[part.key] ?? ""}
                    disabled={!canEdit}
                    onChange={(event) => setReasoning((current) => ({ ...current, [part.key]: event.target.value }))}
                  >
                    <option value="">Reasoning: default</option>
                    <option value="none">None</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </Select>
                </div>
              </Field>
            ))}
          </div>
        ) : null}
        <div className="pf-admin-tune__actions">
          <Button
            variant="primary"
            disabled={!canEdit}
            onClick={() => {
              const cleanTemperatures: Record<string, number> = {};
              for (const [key, value] of Object.entries(temperatures)) {
                const number = Number(value);
                if (value.trim() !== "" && Number.isFinite(number)) cleanTemperatures[key] = number;
              }
              const cleanReasoning: Record<string, string> = {};
              for (const [key, value] of Object.entries(reasoning)) {
                if (value) cleanReasoning[key] = value;
              }
              const cleanTokens: Record<string, number> = {};
              for (const [key, value] of Object.entries(maxTokens)) {
                const number = Number(value);
                if (value.trim() !== "" && Number.isFinite(number)) cleanTokens[key] = number;
              }
              void onSave({
                purpose_models: purposes,
                purpose_temperatures: cleanTemperatures,
                purpose_reasoning: cleanReasoning,
                purpose_max_tokens: cleanTokens,
              });
            }}
          >
            Save models
          </Button>
          <Button
            disabled={!canEdit}
            onClick={() => {
              setPurposes({});
              setTemperatures({});
              setReasoning({});
              setMaxTokens({});
              void onSave({
                purpose_models: {},
                purpose_temperatures: {},
                purpose_reasoning: {},
                purpose_max_tokens: {},
              });
            }}
          >
            Use the default for every part
          </Button>
        </div>
        <Text size="xs" tone="tertiary">
          A blank reply cap uses the cap built into that part's call (the planner's cap grows
          with the trip, up to 16,000; the verifier is 1,500 and the editor 3,000). A blank
          temperature uses the part's default. Reasoning effort is sent only to providers
          that support it; other providers store it but do not use it.
        </Text>
      </div>

      <div className="pf-card pf-eval__panel">
        <Text size="sm" weight="semibold">Turn routing</Text>
        <Text size="xs" tone="tertiary">
          The analyst is the default router. Jev (System 1) can make the plan / modify / ask / clarify routing decision before the analyst extracts the intake slots; if Jev is unreachable, the analyst's own route is kept.
        </Text>
        <Field label="Who chooses the path?" wide>
          <Select
            value={mode}
            disabled={!canEdit}
            onChange={(event) => setMode(event.target.value === "jev" ? "jev" : "analyst")}
          >
            <option value="analyst">Analyst (LLM, default)</option>
            <option value="jev">Jev — System 1 gate</option>
          </Select>
        </Field>
        {mode === "jev" ? (
          <>
            <div className="pf-form-grid">
              <Field label="Jev endpoint" hint="The system-one URL, for example https://api.typesafe.ai/v1/systemone." wide>
                <Input value={jevUrl} disabled={!canEdit} onChange={(event) => setJevUrl(event.target.value)} />
              </Field>
              <Field label="Jev model">
                <Input value={jevModel} disabled={!canEdit} onChange={(event) => setJevModel(event.target.value)} />
              </Field>
              <Field
                label="Jev API key"
                hint={orchestrator.jev.key_set ? `A key is saved (…${orchestrator.jev.key_tail}). Leave blank to keep it.` : "No Jev key saved yet."}
                wide
              >
                <Input
                  type="password"
                  value={jevKey}
                  disabled={!canEdit}
                  placeholder={orchestrator.jev.key_set ? "Leave blank to keep the saved key" : "Paste the Jev key"}
                  onChange={(event) => setJevKey(event.target.value)}
                />
              </Field>
            </div>
            {!orchestrator.jev.key_set && !jevKey ? (
              <Text size="xs" tone="tertiary">
                Without a key, Jev is skipped and the analyst makes the routing decision.
              </Text>
            ) : null}
          </>
        ) : null}
        <div className="pf-admin-tune__actions">
          <Button
            variant="primary"
            disabled={!canEdit}
            onClick={() =>
              void onSave({
                orchestrator: {
                  mode,
                  jev: {
                    model: jevModel,
                    url: jevUrl,
                    ...(jevKey.trim() ? { api_key: jevKey.trim() } : {}),
                  },
                },
              })
            }
          >
            Save routing
          </Button>
        </div>
      </div>
    </div>
  );
}
