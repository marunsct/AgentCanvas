/** Config form generated from a node type's JSON Schema (RJSF, M2-T7). Edits become merge-patch commands. */
import Form from "@rjsf/core";
import { BUTTON_TEMPLATES, withItemDefaults } from "../ui/rjsfTemplates";
import { validator } from "../ui/validator";
import { useMemo } from "react";
import type { NodeManifest } from "../api/types";
import { routeEdgeChanges } from "../canvas/connect";
import { useEditor } from "../editor/store";
import type { IRNode, IRResource } from "../ir/model";
import { JsonSchemaField } from "./JsonSchemaField";
import { SecretRefField } from "./SecretRefField";
import { SubgraphField } from "./SubgraphField";
import { diffToMergePatch, uiSchemaFromFormats } from "./schema";
import { WIDGETS } from "./widgets";

const FIELDS = { json_schema: JsonSchemaField, subgraph: SubgraphField, secret_ref: SecretRefField };

export function ConfigForm({ element, manifest, isResource }: {
  element: IRNode | IRResource; manifest: NodeManifest; isResource: boolean;
}) {
  const dispatch = useEditor((s) => s.dispatch);
  const schema = useMemo(() => withItemDefaults(manifest.config_schema as Record<string, unknown>), [manifest.config_schema]);
  const uiSchema = useMemo(() => ({ ...uiSchemaFromFormats(schema), "ui:submitButtonOptions": { norender: true } }), [schema]);
  const config = (element.config ?? {}) as Record<string, unknown>;

  return (
    <Form
      schema={schema} uiSchema={uiSchema} formData={config} validator={validator} widgets={WIDGETS} fields={FIELDS}
      templates={{ ButtonTemplates: BUTTON_TEMPLATES }}
      liveValidate showErrorList={false} noHtml5Validate idPrefix={`cfg_${element.id}`}
      onChange={(e) => {
        const next = (e.formData ?? {}) as Record<string, unknown>;
        const patch = diffToMergePatch(config, next);
        if (!Object.keys(patch).length) return;
        const cmd = isResource
          ? { op: "UpdateResource" as const, id: element.id, patch: { config: patch } }
          : { op: "UpdateNode" as const, id: element.id, patch: { config: patch } };
        // a router's arrows follow its renamed paths and go with its removed ones, in the same undo step
        const wf = useEditor.getState().wf;
        const edges = !isResource && manifest.dynamic_outputs === "routes" && wf ? routeEdgeChanges(wf, element as IRNode, next) : [];
        dispatch([cmd, ...edges], { debounceMs: 400 });
      }}
    />
  );
}
