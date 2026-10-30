/** Red team (G-10): the fields that turn an experiment into an attack on the workflow. */
export const CATEGORIES: Record<string, string> = {
  prompt_injection: "Prompt injection",
  jailbreak: "Jailbreak",
  pii_extraction: "PII extraction",
  off_topic: "Off-topic",
  multi_turn: "Multi-turn (chat workflows)",
};

export interface RedTeamDraft {
  on: boolean;
  categories: string[];
  per_category: string;
  attacker: string;
  judge: string;
  policy: string;
}

// multi-turn attacks need a chat workflow, so they are opt-in
export const NEW_RED_TEAM: RedTeamDraft = {
  on: false, categories: Object.keys(CATEGORIES).filter((k) => k !== "multi_turn"),
  per_category: "4", attacker: "", judge: "", policy: "",
};

/** The POST /workflows/{id}/red-team body for a draft (and the targets the experiment form has). */
export function redTeamBody(rt: RedTeamDraft, targets: unknown[]): Record<string, unknown> {
  const n = Math.round(Number(rt.per_category));
  return {
    categories: rt.categories,
    per_category: Number.isFinite(n) ? Math.min(10, Math.max(1, n)) : 4,
    attacker_model: rt.attacker.trim() || null,
    judge_model: rt.judge.trim() || null,
    policy: rt.policy.trim(),
    targets,
  };
}

export function RedTeamFields({ value, onChange }: { value: RedTeamDraft; onChange: (v: RedTeamDraft) => void }) {
  const set = (patch: Partial<RedTeamDraft>) => onChange({ ...value, ...patch });
  return (
    <div className="col gap small" data-testid="red-team">
      <div className="row gap wrap">
        {Object.entries(CATEGORIES).map(([k, label]) => (
          <label key={k} className="row">
            <input type="checkbox" checked={value.categories.includes(k)}
              onChange={(e) => set({ categories: e.target.checked ? [...value.categories, k] : value.categories.filter((c) => c !== k) })} />{label}
          </label>
        ))}
        <label className="row">attacks each
          <input aria-label="attacks per category" value={value.per_category} onChange={(e) => set({ per_category: e.target.value })} style={{ width: 40 }} />
        </label>
      </div>
      <div className="row gap wrap">
        <input aria-label="attacker model" placeholder="attacks written by (empty: the built-in list)" value={value.attacker}
          onChange={(e) => set({ attacker: e.target.value })} style={{ width: 270 }} />
        <input aria-label="judge model" placeholder="judge (empty: rules decide)" value={value.judge}
          onChange={(e) => set({ judge: e.target.value })} style={{ width: 220 }} />
        <input aria-label="red team policy" placeholder="what this workflow is for and must never do" value={value.policy}
          onChange={(e) => set({ policy: e.target.value })} style={{ width: 330 }} />
      </div>
    </div>
  );
}
