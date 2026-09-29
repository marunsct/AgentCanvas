/** Config form generated from a node type's JSON Schema (RJSF, M2-T7). Edits become merge-patch commands. */
import Form from "@rjsf/core";
import { customizeValidator } from "@rjsf/validator-ajv8";
import { useMemo } from "react";
import type { NodeManifest } from "../api/types";
import { useEditor } from "../editor/store";
import type { IRNode, IRResource } from "../ir/model";
import { JsonSchemaField } from "./JsonSchemaField";
import { CUSTOM_FORMATS, diffToMergePatch, uiSchemaFromFormats } from "./schema";
import { WIDGETS } from "./widgets";

const validator = customizeValidator({
  customFormats: Object.fromEntries(CUSTOM_FORMATS.map((f) => [f, () => true])),
});
const FIELDS = { json_schema: JsonSchemaField };

export function ConfigForm({ element, manifest, isResource }: {
  element: IRNode | IRResource; manifest: NodeManifest; isResource: boolean;
}) {
  const dispatch = useEditor((s) => s.dispatch);
  const schema = manifest.config_schema as Record<string, unknown>;
  const uiSchema = useMemo(() => ({ ...uiSchemaFromFormats(schema), "ui:submitButtonOptions": { norender: true } }), [schema]);
  const config = (element.config ?? {}) as Record<string, unknown>;

  return (
    <Form
      schema={schema} uiSchema={uiSchema} formData={config} validator={validator} widgets={WIDGETS} fields={FIELDS}
      liveValidate showErrorList={false} noHtml5Validate idPrefix={`cfg_${element.id}`}
      onChange={(e) => {
        const patch = diffToMergePatch(config, (e.formData ?? {}) as Record<string, unknown>);
        if (!Object.keys(patch).length) return;
        const cmd = isResource
          ? { op: "UpdateResource" as const, id: element.id, patch: { config: patch } }
          : { op: "UpdateNode" as const, id: element.id, patch: { config: patch } };
        dispatch([cmd], { debounceMs: 400 });
      }}
    />
  );
}
