/** API types: generated from the OpenAPI document (`pnpm gen:api`) plus a few narrowings. */
import type * as G from "./generated";
import type { Diagnostic as GDiagnostic } from "./generated";

export type NodeManifest = G.NodeManifest;
export type PortSpec = G.PortSpec;
export type ModelInfo = G.ModelInfo;
export type TemplateInfo = G.TemplateInfo;
export type WorkflowSummary = G.WorkflowSummary;
export type Diagnostic = GDiagnostic;

export interface WorkflowPage {
  items: WorkflowSummary[];
  next_cursor: string | null;
}

export type Position = { x: number; y: number };
export type Layout = Record<string, Position>;
