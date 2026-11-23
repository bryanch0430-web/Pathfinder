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
  { key: "understand", label: "Analyst (intent + slots)", hint: "Reads the message, routes the turn and extracts the intake slots." },
  { key: "plan", label: "Planner (composer)", hint: "Chooses the stops, order and timings for a new plan." },
  { key: "edit", label: "Editor", hint: "Turns a change request into typed edit operations." },
  { key: "verify", label: "Constraint verifier", hint: "Audits the traveller’s hard constraints against the plan and the sources." },
  { key: "ask", label: "Ask / quick answers", hint: "Answers a question about the current plan." },
  { key: "memory", label: "Memory", hint: "Extracts durable preferences after a finalised trip." },
];

/**
 * Model routing for the parts of one turn. All parts share the active provider
 * key (BYOK included); only the model name can differ here. The writer card above
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
              Each part can use its own model; leave a field blank to use the default writer
              (<span className="pf-mono">{models.default || "not set"}</span>). Each part can also set
              its own temperature and reasoning effort; all parts use the active provider key,
              including a BYOK key.
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
                    placeholder="Default model"
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
          Temperature applies to every model. A blank reply cap uses the call's built-in cap
          (planner 12,000; editor 2,000; verifier 1,500; others 4,096). Reasoning effort is sent only
          to OpenAI reasoning models (GPT-5 / o-series); other providers store it but do not use it.
        </Text>
      </div>

      <div className="pf-card pf-eval__panel">
        <Text size="sm" weight="semibold">Intent routing</Text>
        <Text size="xs" tone="tertiary">
          The analyst is the default intent router. Jev can make the plan / modify / ask / clarify routing decision before the analyst extracts the intake slots; if Jev is unreachable, the analyst's own route is kept.
        </Text>
        <Field label="Who chooses the path?" wide>
          <Select
            value={mode}
            disabled={!canEdit}
            onChange={(event) => setMode(event.target.value === "jev" ? "jev" : "analyst")}
          >
            <option value="analyst">Intent router (LLM, default)</option>
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
            Save orchestrator
          </Button>
        </div>
      </div>
    </div>
  );
}
