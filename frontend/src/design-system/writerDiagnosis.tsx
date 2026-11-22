import { useEffect, useState } from "react";
import { getConfig } from "../lib/api";
import { useI18n, type MessageKey } from "../lib/i18n";
import { Button, Text } from "./atoms";

/** What a writer failure actually is, when the raw line is only an HTTP status. */
export function writerDiagnosis(text: string): "credit" | "auth" | "rate" | null {
  const folded = text.toLowerCase();
  if (folded.includes("http 402") || /credit|quota|billing|insufficient/.test(folded)) return "credit";
  if (folded.includes("http 401")) return "auth";
  if (folded.includes("http 429")) return "rate";
  return null;
}

export type Situation = "wish" | "buffer" | "emptyAdd" | "checkAnswer";

/** A pointed reading of a failed turn, from the line and the reply. No writer call. */
export function readSituation(input: string, output: string): Situation | null {
  const said = `${input}\n${output}`.toLowerCase();
  const reply = output.toLowerCase();
  if (reply.includes("was left out") && reply.includes("reach your stay")) return "buffer";
  if (reply.includes("did not return a located") || (reply.includes("was not added") && said.includes("where"))) return "wish";
  if (reply.includes("no itinerary items needed to change") && /\badd\b/.test(input.toLowerCase())) return "emptyAdd";
  if (reply.includes("nothing in the plan matched") && /ok fix|relax|flight arrival/.test(said)) return "checkAnswer";
  return null;
}

export function SituationReading({ input, output }: { input: string; output: string }) {
  const { t } = useI18n();
  const kind = readSituation(input, output);
  if (!kind) return null;
  const title = `situation.${kind}.title` as MessageKey;
  const cause = `situation.${kind}.cause` as MessageKey;
  const fix = `situation.${kind}.fix` as MessageKey;
  return (
    <div className="pf-cases__note">
      <Text size="xs" weight="semibold">{t(title)}</Text>
      <Text size="xs" tone="secondary">{t(cause)}</Text>
      <Text size="xs">{t(fix)}</Text>
    </div>
  );
}

/** The situation, the cause, and the fix. A 402 is the writer account, not a failed case. */
export function WriterDiagnosis({ text }: { text: string }) {
  const { t } = useI18n();
  const kind = writerDiagnosis(text);
  const [writer, setWriter] = useState("");
  useEffect(() => {
    if (!kind) return;
    getConfig()
      .then((config) => setWriter(String(config.writer_model || "")))
      .catch(() => setWriter(""));
  }, [kind]);
  if (!kind) return null;
  const openSettings = () => {
    window.location.hash = "settings";
  };
  const title = `writerFix.${kind}.title` as MessageKey;
  const situation = `writerFix.${kind}.situation` as MessageKey;
  const fix = `writerFix.${kind}.fix` as MessageKey;
  return (
    <section className="pf-card pf-cases__note" role="status">
      <Text size="sm" weight="semibold">{t(title)}</Text>
      <Text size="sm" tone="secondary">
        {t(situation, { writer: writer || t("eval.notLinked") })}
      </Text>
      <Text size="sm">{t(fix)}</Text>
      <div className="pf-row">
        <Button size="sm" variant="primary" onClick={openSettings}>{t("writerFix.openSettings")}</Button>
      </div>
    </section>
  );
}
