/** Add an example to a dataset with one field per workflow input and expected output, instead of typing JSON;
 * "As JSON" stays for nested values. A field's text is read as JSON when it is a number, true/false, a list or an
 * object, and as text otherwise. */
import { useState } from "react";

interface Channel { name: string; io?: string | null }

export function readValue(text: string): unknown {
  const t = text.trim();
  if (/^(-?\d+(\.\d+)?|true|false|null|\[.*\]|\{.*\})$/s.test(t)) {
    try { return JSON.parse(t); } catch { /* text after all */ }
  }
  return text;
}

function collect(values: Record<string, string>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, readValue(v)]));
}

export function ExampleForm({ channels, busy, onAdd }: {
  channels: Channel[];
  busy: boolean;
  onAdd: (example: { input: Record<string, unknown>; reference: Record<string, unknown>; split: string }) => void;
}) {
  const open = channels.filter((c) => c.io !== "private");
  const marked = open.some((c) => c.io === "in" || c.io === "out");
  const ins = marked ? open.filter((c) => c.io !== "out") : open;
  const outs = marked ? open.filter((c) => c.io !== "in") : open;
  const [input, setInput] = useState<Record<string, string>>({});
  const [reference, setReference] = useState<Record<string, string>>({});
  const [json, setJson] = useState(ins.length === 0);
  const [rawIn, setRawIn] = useState("{}");
  const [rawRef, setRawRef] = useState("");
  const [split, setSplit] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = () => {
    setError(null);
    try {
      const example = json
        ? { input: JSON.parse(rawIn || "{}"), reference: rawRef.trim() ? JSON.parse(rawRef) : {}, split }
        : { input: collect(input), reference: collect(reference), split };
      onAdd(example);
      setReference({});
      setRawRef("");
    } catch (e) {
      setError(`Not valid JSON: ${(e as Error).message}`);
    }
  };
  return (
    <form className="policy-form example-form" aria-label="New example" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      {json ? (
        <div className="row gap wrap">
          <input aria-label="example input" placeholder='input, e.g. {"question": "…"}' value={rawIn} onChange={(e) => setRawIn(e.target.value)} />
          <input aria-label="example reference" placeholder='expected output, e.g. {"answer": "…"}' value={rawRef} onChange={(e) => setRawRef(e.target.value)} />
        </div>
      ) : (
        <div className="example-fields">
          <fieldset><legend>Input</legend>
            {ins.map((c) => (
              <label key={c.name}>{c.name}
                <input aria-label={`input ${c.name}`} value={input[c.name] ?? ""} onChange={(e) => setInput({ ...input, [c.name]: e.target.value })} />
              </label>
            ))}
          </fieldset>
          <fieldset><legend>Expected output <span className="muted small">(empty fields are not checked)</span></legend>
            {outs.map((c) => (
              <label key={c.name}>{c.name}
                <input aria-label={`expected ${c.name}`} value={reference[c.name] ?? ""} onChange={(e) => setReference({ ...reference, [c.name]: e.target.value })} />
              </label>
            ))}
          </fieldset>
        </div>
      )}
      <div className="row gap">
        <input aria-label="example split" placeholder="split (e.g. test)" value={split} onChange={(e) => setSplit(e.target.value)} style={{ width: 140 }} />
        <button type="submit" disabled={busy}>Add example</button>
        <label className="row small"><input type="checkbox" checked={json} onChange={(e) => setJson(e.target.checked)} /> As JSON</label>
      </div>
      {error && <p className="error-text small" role="alert">{error}</p>}
    </form>
  );
}
