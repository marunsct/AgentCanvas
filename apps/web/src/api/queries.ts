/** Server data hooks (TanStack Query). Catalog data is static per server build. */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { apiFetch } from "./client";
import type { ModelInfo, NodeManifest, TemplateInfo, WorkflowPage } from "./types";

export const PROJECT_ID = "prj_default"; // single project until accounts land (Sprint 10)

export function useNodeTypes() {
  return useQuery({ queryKey: ["node-types"], queryFn: () => apiFetch<NodeManifest[]>("/node-types"), staleTime: Infinity });
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
