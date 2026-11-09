/** A choice among the models set up to answer here (a key, or a local server that lists them). A saved model that
 * is not set up any more stays shown, marked, so the page does not silently change it. */
import type { ModelOption } from "../api/generated";

export function ModelSelect({ options, loading, value, onChange, empty, label, disabled }: {
  options: ModelOption[] | undefined;
  loading?: boolean;
  value: string;
  onChange: (id: string) => void;
  empty?: string; // the "nothing chosen" entry, e.g. "the project's model"
  label: string;
  disabled?: boolean;
}) {
  const known = (options ?? []).some((m) => m.id === value);
  return (
    <select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {empty !== undefined && <option value="">{empty}</option>}
      {(options ?? []).map((m) => <option key={m.id} value={m.id} title={m.id}>{m.label} · {m.provider}</option>)}
      {value && !known && (
        <option value={value}>{loading ? value : `${value.slice(value.indexOf(":") + 1)} (not set up now)`}</option>
      )}
      {loading && <option disabled value="__loading">Checking which models are set up…</option>}
    </select>
  );
}
