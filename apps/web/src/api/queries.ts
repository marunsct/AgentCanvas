/** Server data hooks (TanStack Query). Catalog data is static per server build. */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { apiFetch } from "./client";
import type { ModelInfo, NodeManifest, TemplateInfo, WorkflowPage } from "./types";

export const PROJECT_ID = "prj_default"; // single project until accounts land (Sprint 10)

export function useNodeTypes() {
  // includes the project's workspace custom nodes, so it refreshes quickly after workspace edits (FR-CN-01)
  return useQuery({
    queryKey: ["node-types", PROJECT_ID],
    queryFn: () => apiFetch<NodeManifest[]>(`/node-types?project_id=${PROJECT_ID}`),
    staleTime: 2_000,
  });
}

export interface WorkspaceCatalog {
  tools: { name: string; import_path: string; description: string }[];
  nodes: string[];
  scripts: { name: string; import_path: string; description: string }[];
  handlers: { name: string; type: string; filter: string | null; import_path: string; targets: string[] }[];
  problems: { path: string; message: string }[];
}

export function useWorkspaceCatalog() {
  return useQuery({
    queryKey: ["workspace-catalog", PROJECT_ID],
    queryFn: () => apiFetch<WorkspaceCatalog>(`/projects/${PROJECT_ID}/workspace/catalog`),
    staleTime: 2_000,
  });
}

export function useManifests(): Map<string, NodeManifest> {
  const { data } = useNodeTypes();
  return useMemo(() => new Map((data ?? []).map((m) => [m.type, m])), [data]);
}

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => apiFetch<ModelInfo[]>("/models"), staleTime: Infinity });
}

export function useTemplates() {
  return useQuery({ queryKey: ["templates"], queryFn: () => apiFetch<TemplateInfo[]>("/templates"), staleTime: 60_000 });
}

export function useWorkflows() {
  return useQuery({
    queryKey: ["workflows", PROJECT_ID],
    queryFn: () => apiFetch<WorkflowPage>(`/projects/${PROJECT_ID}/workflows?limit=200`),
  });
}
