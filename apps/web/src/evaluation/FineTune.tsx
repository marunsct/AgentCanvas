/** Fine-tuning export (G-10): accepted trajectories, or a dataset, as JSONL training examples. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiDownload, apiFetch } from "../api/client";
import type { DatasetOut, FinetunePreview } from "../api/generated";
import { errorText } from "../ui/QueryStatus";

export interface FineTuneDraft {
  source: "runs" | "dataset";
  format: "openai" | "messages";
  min_score: string;
  include_online: boolean;
  dataset_id: string;
  split: string;
  system: string;
}

export const NEW_FINE_TUNE: FineTuneDraft = {
  source: "runs", format: "openai", min_score: "0.8", include_online: false, dataset_id: "", split: "", system: "",
};

/** The request body for a draft; an empty system box leaves the workflow's own prompt, "-" sends none. */
export function fineTuneBody(d: FineTuneDraft, firstDataset?: string): Record<string, unknown> {
  const score = Number(d.min_score);
  return {
    source: d.source, format: d.format,
    min_score: Number.isFinite(score) ? Math.min(1, Math.max(0, score)) : 0.8,
    include_online: d.include_online,
    dataset_id: d.source === "dataset" ? d.dataset_id || firstDataset || null : null,
    split: d.source === "dataset" && d.split.trim() ? d.split.trim() : null,
    system: d.system.trim() === "-" ? "" : d.system.trim() || null,
  };
}

export function FineTune({ wfId }: { wfId: string }) {
  const [d, setD] = useState<FineTuneDraft>(NEW_FINE_TUNE);
  const datasets = useQuery({ queryKey: ["datasets", wfId], queryFn: () => apiFetch<DatasetOut[]>(`/workflows/${wfId}/datasets`) });
  const url = `/workflows/${wfId}/finetune-export`;
  const body = fineTuneBody(d, datasets.data?.[0]?.id);
  const preview = useMutation({ mutationFn: () => apiFetch<FinetunePreview>(`${url}?preview=true`, { method: "POST", json: body }) });
  const download = useMutation({ mutationFn: () => apiDownload(url, "finetune.jsonl", body) });
  const set = (patch: Partial<FineTuneDraft>) => { preview.reset(); setD((x) => ({ ...x, ...patch })); };
  return (
    <section className="pad" data-testid="finetune">
      <p className="muted small">
        Turn good conversations into training examples (one JSON object per line) for fine-tuning a model. Runs count
        when people scored them (annotation reviews, thumbs up or down) well enough and nobody gave a thumbs-down.
      </p>
      <div className="policy-form">
        <div className="row gap wrap">
          <select aria-label="fine-tuning source" value={d.source} onChange={(e) => set({ source: e.target.value as FineTuneDraft["source"] })}>
            <option value="runs">Accepted runs</option>
            <option value="dataset">A dataset</option>
          </select>
          <select aria-label="fine-tuning format" value={d.format} onChange={(e) => set({ format: e.target.value as FineTuneDraft["format"] })}>
            <option value="openai">OpenAI chat fine-tuning</option>
            <option value="messages">Plain messages</option>
          </select>
          {d.source === "runs" ? (
            <>
              <label className="row small">scored at least
                <input aria-label="minimum score" value={d.min_score} onChange={(e) => set({ min_score: e.target.value })} style={{ width: 50 }} />
              </label>
              <label className="row small" title="scores from online evaluators count as well as people's">
                <input type="checkbox" checked={d.include_online} onChange={(e) => set({ include_online: e.target.checked })} />online scores too
              </label>
            </>
          ) : (
            <>
              <select aria-label="fine-tuning dataset" value={d.dataset_id} onChange={(e) => set({ dataset_id: e.target.value })}>
                {datasets.data?.map((x) => <option key={x.id} value={x.id}>{x.name} (v{x.version}, {x.examples} examples)</option>)}
              </select>
              <input aria-label="fine-tuning split" placeholder="split (all)" value={d.split} onChange={(e) => set({ split: e.target.value })} style={{ width: 110 }} />
            </>
          )}
        </div>
        <input aria-label="system message" placeholder="system message (empty: the workflow's own prompt; - for none)" value={d.system}
          onChange={(e) => set({ system: e.target.value })} style={{ maxWidth: 520 }} />
        <div className="row gap">
          <button type="button" disabled={preview.isPending} onClick={() => preview.mutate()}>Preview</button>
          <button type="button" className="primary" disabled={download.isPending} onClick={() => download.mutate()}>Download JSONL</button>
        </div>
        {(preview.error || download.error) && <p role="alert" className="error-text">{errorText(preview.error ?? download.error)}</p>}
        {preview.data && (
          <div data-testid="finetune-preview">
            <p><b>{preview.data.examples}</b> training example{preview.data.examples === 1 ? "" : "s"}
              {preview.data.skipped.length > 0 && <>, {preview.data.skipped.length} left out</>}.</p>
            <ul className="small muted">
              {preview.data.skipped.slice(0, 8).map((s) => <li key={s.ref}><code>{s.ref}</code>: {s.reason}</li>)}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
