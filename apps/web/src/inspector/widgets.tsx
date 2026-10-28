/** Custom RJSF widgets for AgentCanvas config formats (M2-T7). */
import type { WidgetProps } from "@rjsf/utils";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useShallow } from "zustand/react/shallow";
import { apiFetch } from "../api/client";
import { PROJECT_ID, useModelProviders, useModels } from "../api/queries";
import type { ProviderInfo } from "../api/types";
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

/** Where a provider's models get their key or server from, in one line under the model field. */
export function providerHint(p: ProviderInfo): string {
  const parts = [p.label];
  if (!p.installed) parts.push(`not installed on this server (install ${p.package})`);
  if (p.key_env) parts.push(`key: the project secret or server variable ${p.key_env}`);
  if (p.local && p.base_url) parts.push(`runs on your machine, default ${p.base_url}`);
  if (p.optional_key_env) parts.push(`optional key: ${p.optional_key_env}`);
  if (p.base_url_required) parts.push("set the Base URL below");
  if (p.notes) parts.push(p.notes);
  return parts.join(" · ");
}

export function ModelWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly } = props;
  const { data } = useModels();
  const providers = useModelProviders().data ?? [];
  const text = typeof value === "string" ? value : "";
  const provider = providers.find((p) => p.id === text.split(":")[0]);
  return (
    <>
      <input id={id} list={`${id}-models`} value={text} disabled={disabled || readonly}
        placeholder="provider:model" onChange={(e) => onChange(e.target.value)} />
      <datalist id={`${id}-models`}>
        {(data ?? []).filter((m) => m.provider !== "fake").map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        {providers.map((p) => <option key={p.id} value={`${p.id}:`}>{p.label}: {p.example}</option>)}
      </datalist>
      {text.includes(":") && (
        <p className="small muted" data-testid="model-provider-hint">
          {provider ? providerHint(provider) : `Unknown provider "${text.split(":")[0]}": AgentCanvas passes it to LangChain's init_chat_model as is.`}
        </p>
      )}
    </>
  );
}

export function MonoWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly, placeholder } = props;
  return <input id={id} className="mono" value={typeof value === "string" ? value : ""} placeholder={placeholder}
    disabled={disabled || readonly} onChange={(e) => onChange(e.target.value)} />;
}

/** A knowledge base of this project, chosen from the list (a name no knowledge base has stays visible, marked). */
export function KnowledgeBaseWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly } = props;
  const bases = useQuery({ queryKey: ["knowledge", PROJECT_ID], queryFn: () => apiFetch<{ name: string; documents: number }[]>(`/projects/${PROJECT_ID}/knowledge`) });
  const names = (bases.data ?? []).map((b) => b.name);
  const known = typeof value !== "string" || !value || !bases.data || names.includes(value);
  return (
    <>
      <select id={id} value={typeof value === "string" ? value : ""} disabled={disabled || readonly}
        onChange={(e) => onChange(e.target.value || undefined)} className={known ? "" : "invalid"}>
        <option value="">— choose a knowledge base —</option>
        {!known && <option value={value as string}>{value as string} (not in this project)</option>}
        {bases.data?.map((b) => <option key={b.name} value={b.name}>{b.name} ({b.documents} documents)</option>)}
      </select>
      {bases.data && bases.data.length === 0 && <p className="small muted">No knowledge bases yet: add one on the <Link to="/knowledge">Knowledge</Link> page.</p>}
    </>
  );
}

export const WIDGETS = {
  prompt: PromptWidget, template: PromptWidget, channel: ChannelWidget, cel: CelWidget, model: ModelWidget,
  import_path: MonoWidget, regex: MonoWidget, url_template: MonoWidget, knowledge_base: KnowledgeBaseWidget,
};
