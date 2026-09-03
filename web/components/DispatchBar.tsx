"use client";

import { useEffect, useState } from "react";
import {
  DispatchApplyResponse,
  DispatchChange,
  api,
} from "@/lib/api";

interface Props {
  day: string;
  runId: number | null;
  onCommitted: () => void;
}

type Stage = "idle" | "parsing" | "parsed" | "previewing" | "preview" | "committing";

/**
 * Natural-language dispatch, as a strict three-step flow.
 *
 * type -> PARSE -> the structured change is shown -> PREVIEW -> the diff is
 * shown -> CONFIRM. Nothing is written before the last step, and the
 * dispatcher sees both what the model understood and what it would do.
 *
 * The parsed change is displayed as a typed object rather than as prose,
 * deliberately: "remove_technician T3" is checkable at a glance in a way
 * "I'll remove Ahmad" is not.
 */
export default function DispatchBar({ day, runId, onCommitted }: Props) {
  const [text, setText] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [change, setChange] = useState<DispatchChange | null>(null);
  const [preview, setPreview] = useState<DispatchApplyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState<{
    provider: string;
    model: string;
    ready: boolean;
    reason: string | null;
  } | null>(null);

  useEffect(() => {
    api.dispatchProvider().then(setProvider).catch(() => setProvider(null));
  }, []);

  const reset = () => {
    setStage("idle");
    setChange(null);
    setPreview(null);
    setError(null);
  };

  async function doParse() {
    if (!text.trim()) return;
    setStage("parsing");
    setError(null);
    setPreview(null);
    try {
      const res = await api.dispatchParse(text, day, runId ?? undefined);
      if (!res.understood || !res.change) {
        setError(res.error ?? "could not understand that");
        setStage("idle");
        return;
      }
      setChange(res.change);
      setStage("parsed");
    } catch (e) {
      setError(String(e));
      setStage("idle");
    }
  }

  async function doPreview() {
    if (!change || runId === null) return;
    setStage("previewing");
    try {
      const res = await api.dispatchApply(runId, change, "12:00", false);
      setPreview(res);
      setStage("preview");
      if (!res.ok) setError(res.reason);
    } catch (e) {
      setError(String(e));
      setStage("parsed");
    }
  }

  async function doCommit() {
    if (!change || runId === null) return;
    setStage("committing");
    try {
      await api.dispatchApply(runId, change, "12:00", true);
      setText("");
      reset();
      onCommitted();
    } catch (e) {
      setError(String(e));
      setStage("preview");
    }
  }

  const busy = ["parsing", "previewing", "committing"].includes(stage);

  return (
    <div className="dispatch">
      <div className="dispatch-input">
        <input
          value={text}
          placeholder='e.g. "Ahmad called in sick, redistribute his jobs"'
          onChange={(e) => {
            setText(e.target.value);
            if (stage !== "idle") reset();
          }}
          onKeyDown={(e) => e.key === "Enter" && !busy && doParse()}
          disabled={busy || runId === null}
        />
        <button onClick={doParse} disabled={busy || !text.trim() || runId === null}>
          {stage === "parsing" ? "reading…" : "Parse"}
        </button>
      </div>

      {provider && !provider.ready && (
        <p className="dispatch-warn">
          No LLM configured — {provider.reason}. Set <code>DEEPSEEK_API_KEY</code>{" "}
          in <code>.env</code>, or <code>LLM_PROVIDER=ollama</code> to use the
          local model.
        </p>
      )}

      {error && <p className="dispatch-error">{error}</p>}

      {change && (
        <div className="dispatch-change">
          <h4>The model produced this change — check it before previewing</h4>
          <table>
            <tbody>
              {Object.entries(change)
                .filter(([, v]) => v !== null && v !== undefined && v !== "")
                .map(([k, v]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td>{Array.isArray(v) ? v.join(", ") : String(v)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          {(stage === "parsed" || stage === "previewing") && (
            <div className="dispatch-actions">
              <button onClick={doPreview} disabled={busy}>
                {stage === "previewing" ? "re-solving…" : "Preview the effect"}
              </button>
              <button className="ghost" onClick={reset}>Discard</button>
            </div>
          )}
        </div>
      )}

      {preview && preview.ok && (
        <div className="dispatch-preview">
          <h4>{preview.summary}</h4>
          <div className="preview-stats">
            <span>
              driving{" "}
              <strong className={preview.travel_delta_minutes <= 0 ? "up" : "down"}>
                {preview.travel_delta_minutes > 0 ? "+" : ""}
                {preview.travel_delta_minutes}m
              </strong>
            </span>
            <span>
              unassigned{" "}
              <strong className={preview.unassigned_delta <= 0 ? "up" : "down"}>
                {preview.unassigned_delta > 0 ? "+" : ""}
                {preview.unassigned_delta}
              </strong>
            </span>
            <span>
              customer calls <strong>{preview.customer_calls}</strong>
            </span>
            <span>
              checker{" "}
              <strong className={preview.valid ? "up" : "down"}>
                {preview.valid ? "valid" : "INVALID"}
              </strong>
            </span>
          </div>
          {preview.moves.length > 0 && (
            <ul className="moves">
              {preview.moves.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
          <div className="dispatch-actions">
            <button onClick={doCommit} disabled={busy || !preview.valid}>
              {stage === "committing" ? "applying…" : "Confirm and apply"}
            </button>
            <button className="ghost" onClick={reset}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
