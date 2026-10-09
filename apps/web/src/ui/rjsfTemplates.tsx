/** Settings-form buttons with visible words (S17). RJSF's defaults draw Bootstrap icon fonts this app does not
 * load, so its list buttons (add, remove, move, copy) showed up empty. */
import type { IconButtonProps, SubmitButtonProps, TemplatesType } from "@rjsf/utils";

type Props = IconButtonProps & { className?: string };

function TextButton({ label, name, props }: { label: string; name: string; props: Props }) {
  const { onClick, disabled, id, className, style } = props;
  return (
    <button type="button" id={id} className={`rjsf-btn ${className ?? ""}`.trim()} style={style} onClick={onClick}
      disabled={disabled} aria-label={name} title={name}>
      {label}
    </button>
  );
}

export const BUTTON_TEMPLATES: Partial<TemplatesType["ButtonTemplates"]> = {
  AddButton: (p) => <div className="rjsf-add"><TextButton label="+ Add" name="Add an item" props={p as Props} /></div>,
  RemoveButton: (p) => <TextButton label="Remove" name="Remove this item" props={{ ...(p as Props), className: "danger" }} />,
  MoveUpButton: (p) => <TextButton label="↑ Up" name="Move up" props={p as Props} />,
  MoveDownButton: (p) => <TextButton label="↓ Down" name="Move down" props={p as Props} />,
  CopyButton: (p) => <TextButton label="Copy" name="Copy this item" props={p as Props} />,
  ClearButton: (p) => <TextButton label="Clear" name="Clear this value" props={p as Props} />,
  SubmitButton: ({ uiSchema }: SubmitButtonProps) =>
    uiSchema?.["ui:submitButtonOptions"]?.norender ? null : <button type="submit" className="primary">Submit</button>,
};

/** A new list item starts empty instead of null ("Instance type null is invalid"): array items without a default
 * get the empty value of their type. Returns a copy; the manifest's schema is not changed. */
export function withItemDefaults<T>(schema: T): T {
  const walk = (s: unknown): unknown => {
    if (Array.isArray(s)) return s.map(walk);
    if (!s || typeof s !== "object") return s;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(s as Record<string, unknown>)) out[k] = walk(v);
    const items = out.items as Record<string, unknown> | undefined;
    if (out.type === "array" && items && typeof items === "object" && !Array.isArray(items) && !("default" in items)) {
      const empty = { string: "", integer: 0, number: 0, boolean: false, object: {} }[String(items.type)];
      if (empty !== undefined && !(items.type === "string" && (items.enum || items.format))) out.items = { ...items, default: empty };
    }
    return out;
  };
  return walk(schema) as T;
}
