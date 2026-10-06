/** A secret reference ({"$secret": NAME}), e.g. a chat model's API key: pick a project secret by name. */
import type { FieldProps } from "@rjsf/utils";
import { Link } from "react-router";
import { useSecretNames } from "../api/queries";

export function SecretRefField(props: FieldProps) {
  const { fieldPathId, formData, onChange, disabled, readonly, schema } = props;
  const secrets = useSecretNames();
  const name = (formData as { $secret?: string } | undefined)?.$secret ?? "";
  const id = fieldPathId.$id;
  const known = !name || (secrets.data ?? []).some((s) => s.name === name);
  return (
    <div className="schema-field secret-ref">
      <label htmlFor={id}>{schema.title ?? "Secret"}</label>
      <input id={id} className="mono" list={`${id}-secrets`} value={name} placeholder="secret name, e.g. OPENAI_API_KEY"
        disabled={disabled || readonly} aria-label="API key secret"
        onChange={(e) => onChange(e.target.value.trim() ? { $secret: e.target.value.trim() } : undefined, fieldPathId.path)} />
      <datalist id={`${id}-secrets`}>
        {(secrets.data ?? []).map((s) => <option key={s.name} value={s.name} />)}
      </datalist>
      {!known && <p className="small error-text">No project secret named {name}. Add it on the <Link to="/workspace">Workspace</Link> page.</p>}
      {schema.description && <p className="small muted">{schema.description}</p>}
    </div>
  );
}
