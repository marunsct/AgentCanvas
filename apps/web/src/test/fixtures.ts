import type { NodeManifest } from "../api/types";
import { normalize, type WF } from "../ir/model";
import type { Workflow } from "../ir/generated";
import triageJson from "../../../api/examples/support_triage.ir.json";
import manifestsJson from "../../../../packages/ir-schema/node-types.json";

export const manifests = new Map((manifestsJson as unknown as NodeManifest[]).map((m) => [m.type, m]));
export const triage = (): WF => normalize(structuredClone(triageJson) as unknown as Workflow);
