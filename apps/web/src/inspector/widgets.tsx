/** Custom RJSF widgets for AgentCanvas config formats (M2-T7). */
import type { WidgetProps } from "@rjsf/utils";
import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { apiFetch } from "../api/client";
import { useModels } from "../api/queries";
import { useEditor } from "../editor/store";
import { estimateTokens } from "./schema";

function useChannels(): string[] {
  return useEditor(useShallow((s) => s.wf?.state.channels.map((c) => c.name) ?? NONE));
}

const NONE: string[] = [];

export function PromptWidget(props: WidgetProps) {
  const { id, value, onChange, readonly, disabled } = props;
  const channels = useChannels();
  const ref = useRef<HTMLTextAreaElement>(null);
  const text = typeof value === "string" ? value : "";
  const insert = (ch: string) => {
    const el = ref.current;
    const token = `{{ state.${ch} }}`;
    const at = el?.selectionStart ?? text.length;
    onChange(text.slice(0, at) + token + text.slice(el?.selectionEnd ?? at));
  };
  return (
    <div className="prompt-widget">
      <textarea id={id} ref={ref} value={text} rows={5} disabled={disabled || readonly}
        onChange={(e) => onChange(e.target.value)} spellCheck={false} />
      <div className="chips">
        {channels.map((c) => (
          <button type="button" key={c} className="chip" onClick={() => insert(c)} title={`insert {{ state.${c} }}`}>{c}</button>
        ))}
        <span className="muted">≈ {estimateTokens(text)} tokens</span>
      </div>
    </div>
  );
}

export function ChannelWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly } = props;
  const channels = useChannels();
  const known = typeof value !== "string" || !value || channels.includes(value);
  return (
    <select id={id} value={typeof value === "string" ? value : ""} disabled={disabled || readonly}
      onChange={(e) => onChange(e.target.value || undefined)} className={known ? "" : "invalid"}>
      <option value="">— choose a state channel —</option>
      {!known && <option value={value as string}>{value as string} (not declared)</option>}
      {channels.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

export function CelWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly } = props;
  const [error, setError] = useState<string | null>(null);
  const text = typeof value === "string" ? value : "";
  useEffect(() => {
    if (!text) return setError(null);
    const t = setTimeout(() => {
      apiFetch<{ ok: boolean; error?: string | null }>("/expressions/check", { method: "POST", json: { kind: "cel", source: text } })
        .then((r) => setError(r.ok ? null : r.error ?? "invalid"))
        .catch(() => setError(null));
    }, 300);
    return () => clearTimeout(t);
  }, [text]);
  return (
    <div>
      <input id={id} className={`mono${error ? " invalid" : ""}`} value={text} disabled={disabled || readonly}
        placeholder='state.urgency == "high"' onChange={(e) => onChange(e.target.value)} />
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

export function ModelWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly } = props;
  const { data } = useModels();
  return (
    <>
      <input id={id} list={`${id}-models`} value={typeof value === "string" ? value : ""} disabled={disabled || readonly}
        placeholder="provider:model" onChange={(e) => onChange(e.target.value)} />
      <datalist id={`${id}-models`}>
        {(data ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </datalist>
    </>
  );
}

export function MonoWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly, placeholder } = props;
  return <input id={id} className="mono" value={typeof value === "string" ? value : ""} placeholder={placeholder}
    disabled={disabled || readonly} onChange={(e) => onChange(e.target.value)} />;
}

export const WIDGETS = {
  prompt: PromptWidget, template: PromptWidget, channel: ChannelWidget, cel: CelWidget, model: ModelWidget,
  import_path: MonoWidget, regex: MonoWidget, url_template: MonoWidget,
};
