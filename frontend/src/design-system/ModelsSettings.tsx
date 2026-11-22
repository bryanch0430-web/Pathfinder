import { useState } from "react";
import { Button, Input, Select, Text } from "./atoms";
import { Field } from "./molecules";

type ModelsConfig = {
  default: string;
  purposes: Record<string, string>;
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
  { key: "understand", label: "Analyst (understanding)", hint: "Reads the message, decides the path and extracts slots." },
  { key: "plan", label: "Planner (composer)", hint: "Chooses the stops, order and timings for a new plan." },
  { key: "edit", label: "Editor", hint: "Turns a change request into typed edit operations." },
  { key: "verify", label: "Verifier", hint: "Audits the draft against the request and the sources." },
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
              (<span className="pf-mono">{models.default || "not set"}</span>). All parts use the active
              provider key, including a BYOK key.
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
                <Input
                  value={purposes[part.key] ?? ""}
                  placeholder="Default"
                  disabled={!canEdit}
                  onChange={(event) => setPurposes((current) => ({ ...current, [part.key]: event.target.value }))}
                />
              </Field>
            ))}
          </div>
        ) : null}
        <div className="pf-admin-tune__actions">
          <Button
            variant="primary"
            disabled={!canEdit}
            onClick={() => void onSave({ purpose_models: purposes })}
          >
            Save models
          </Button>
          <Button
            disabled={!canEdit}
            onClick={() => {
              setPurposes({});
              void onSave({ purpose_models: {} });
            }}
          >
            Use the default for every part
          </Button>
        </div>
      </div>

      <div className="pf-card pf-eval__panel">
        <Text size="sm" weight="semibold">Turn orchestrator</Text>
        <Text size="xs" tone="tertiary">
          The analyst is the default router. Jev can choose plan / modify / ask / clarify before the
          analyst extracts the details; if Jev is unreachable, the analyst's own route is kept.
        </Text>
        <Field label="Who chooses the path?" wide>
          <Select
            value={mode}
            disabled={!canEdit}
            onChange={(event) => setMode(event.target.value === "jev" ? "jev" : "analyst")}
          >
            <option value="analyst">Analyst LLM (default)</option>
            <option value="jev">Jev (TypeSafe system-one)</option>
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
                Without a key, Jev is skipped and the analyst routes the turn.
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
