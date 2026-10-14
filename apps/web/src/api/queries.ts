/** Server data hooks (TanStack Query). Catalog data is static per server build. */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { apiFetch } from "./client";
import { can } from "./session";
import type { AdapterManifest, AgentFrameworkOut, ModelInfo, NodeManifest, ProviderInfo, SetupOut, TemplateInfo, WorkflowPage } from "./types";
import type { McpConnector } from "./generated";

/** The project the app works in, chosen at startup (see `chooseProject`); a live binding, so pages read the choice. */
export let PROJECT_ID = "prj_default";

const PROJECT_KEY = "agentcanvas.project";

/** Pick the project for this session: the last one used if it is still visible, else the organisation's first
 * (creating one when the organisation has none). Runs once, after sign-in and before any page loads. */
export async function chooseProject(): Promise<string> {
  let projects = await apiFetch<{ id: string; name: string }[]>("/projects");
  if (projects.length === 0) {
    if (!can("builder")) throw new Error("your organisation has no projects yet; ask a builder or admin to create one");
    projects = [await apiFetch<{ id: string; name: string }>("/projects", { method: "POST", json: { name: "Default project" } })];
  }
  let saved: string | null = null;
  try { saved = localStorage.getItem(PROJECT_KEY); } catch { /* storage blocked */ }
  PROJECT_ID = projects.find((p) => p.id === saved)?.id ?? projects[0].id;
  try { localStorage.setItem(PROJECT_KEY, PROJECT_ID); } catch { /* storage blocked */ }
  return PROJECT_ID;
}

export function useNodeTypes() {
  // includes the project's workspace custom nodes, so it refreshes quickly after workspace edits (FR-CN-01)
  return useQuery({
    queryKey: ["node-types", PROJECT_ID],
    queryFn: () => apiFetch<NodeManifest[]>(`/node-types?project_id=${PROJECT_ID}`),
    staleTime: 2_000,
  });
}

export interface WorkspaceCatalog {
  tools: { name: string; import_path: string; description: string; workflow?: string | null }[];
  nodes: string[];
  scripts: { name: string; import_path: string; description: string; workflow?: string | null }[];
  handlers: { name: string; type: string; filter: string | null; dedup_key: string | null; import_path: string; targets: string[] }[];
  problems: { path: string; message: string; code?: string }[];
  schemas: string[];
}

/** The workspace's tools, scripts, custom blocks and handlers; with `workflowId`, only shared code and that
 * workflow's own (S17). */
export function useWorkspaceCatalog(workflowId?: string | null) {
  return useQuery({
    queryKey: ["workspace-catalog", PROJECT_ID, workflowId ?? null],
    queryFn: () => apiFetch<WorkspaceCatalog>(
      `/projects/${PROJECT_ID}/workspace/catalog${workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : ""}`),
    staleTime: 2_000,
  });
}

/** The one-click MCP connector gallery (S15). */
export function useMcpConnectors() {
  return useQuery({
    queryKey: ["mcp-connectors"],
    queryFn: () => apiFetch<McpConnector[]>("/mcp-connectors"),
    staleTime: Infinity,
  });
}

export function useManifests(): Map<string, NodeManifest> {
  const { data } = useNodeTypes();
  return useMemo(() => new Map((data ?? []).map((m) => [m.type, m])), [data]);
}

/** The frameworks this server can run workflows on (S20): abilities and which blocks each runs. */
export function useFrameworks() {
  return useQuery({ queryKey: ["frameworks"], queryFn: () => apiFetch<AdapterManifest[]>("/frameworks"), staleTime: 30_000 }); // set-up state can change
}

/** Frameworks an Agent block can be designed for, in their own code (S21). */
export function useAgentFrameworks() {
  return useQuery({
    queryKey: ["agent-frameworks"], queryFn: () => apiFetch<AgentFrameworkOut[]>("/agent-frameworks"), staleTime: Infinity,
  });
}

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => apiFetch<ModelInfo[]>("/models"), staleTime: Infinity });
}

/** Model providers (`provider:` prefixes), where each one's key comes from and whether the server has it installed. */
export function useModelProviders() {
  return useQuery({ queryKey: ["model-providers"], queryFn: () => apiFetch<ProviderInfo[]>("/model-providers"), staleTime: Infinity });
}

/** First-run setup of the current project: its default model. */
export function useSetup() {
  return useQuery({ queryKey: ["setup", PROJECT_ID], queryFn: () => apiFetch<SetupOut>(`/projects/${PROJECT_ID}/setup`) });
}

/** Names of the current project's secrets (values never leave the server). */
export function useSecretNames() {
  return useQuery({ queryKey: ["secrets"], queryFn: () => apiFetch<{ name: string }[]>(`/projects/${PROJECT_ID}/secrets`) });
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
