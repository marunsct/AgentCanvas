/** Server data hooks (TanStack Query). Catalog data is static per server build. */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { maturaMode } from "./auth";
import { apiFetch } from "./client";
import { can } from "./session";
import type { AdapterManifest, AgentFrameworkOut, ModelInfo, NodeManifest, ProviderInfo, SetupOut, TemplateInfo, WorkflowPage } from "./types";
import type { McpConnector } from "./generated";

/** The project the app works in, chosen at startup (see `chooseProject`); a live binding, so pages read the choice. */
export let PROJECT_ID = "prj_default";
export let PROJECT_NAME = "Default project";

/** A Matura workspace of the organisation (TS26): projects can be grouped by them in Matura mode. */
export interface Workspace { id: string; name: string; is_default: boolean }
/** The organisation's workspaces (empty outside Matura mode) and the one picked, null = all of them. */
export let WORKSPACES: Workspace[] = [];
export let WORKSPACE_ID: string | null = null;

const PROJECT_KEY = "agentcanvas.project";
const WORKSPACE_KEY = "agentcanvas.workspace";

function stored(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; /* storage blocked */ }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch { /* storage blocked */ }
}

/** Pick the project for this session: the last one used if it is still visible, else the organisation's first
 * (creating one when the organisation has none). In Matura mode only projects of the picked workspace count, and a
 * workspace without one gets one. Runs once, after sign-in and before any page loads, and again on a new workspace. */
export async function chooseProject(): Promise<string> {
  WORKSPACES = maturaMode() ? await apiFetch<Workspace[]>("/workspaces") : [];
  const saved = stored(WORKSPACE_KEY);
  const ws = WORKSPACES.find((w) => w.id === saved) ?? null;
  WORKSPACE_ID = ws?.id ?? null;
  let projects = await apiFetch<{ id: string; name: string }[]>(
    ws ? `/projects?workspace_id=${encodeURIComponent(ws.id)}` : "/projects");
  if (projects.length === 0) {
    const where = ws ? `the workspace '${ws.name}'` : "your organisation";
    if (!can("builder")) throw new Error(`${where} has no projects yet; ask a builder or admin to create one`);
    const body = ws ? { name: `${ws.name} project`, workspace_id: ws.id } : { name: "Default project" };
    projects = [await apiFetch<{ id: string; name: string }>("/projects", { method: "POST", json: body })];
  }
  const chosen = projects.find((p) => p.id === stored(PROJECT_KEY)) ?? projects[0];
  PROJECT_ID = chosen.id;
  PROJECT_NAME = chosen.name;
  store(PROJECT_KEY, PROJECT_ID);
  return PROJECT_ID;
}

/** Work in another project: remembered for next time, and the app starts over in it (pages read the project chosen
 * at start-up). */
export function switchProject(id: string): void {
  store(PROJECT_KEY, id);
  location.assign("/");
}

/** Matura mode: work in another workspace (null = all), moving to one of its projects. Restores the previous
 * workspace when the new one cannot be used (no project in it and no right to make one). */
export async function chooseWorkspace(id: string | null): Promise<string> {
  const before = stored(WORKSPACE_KEY);
  store(WORKSPACE_KEY, id);
  try {
    return await chooseProject();
  } catch (e) {
    store(WORKSPACE_KEY, before);
    await chooseProject();
    throw e;
  }
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

/** A run waiting for a person (an approval or an answer), in any workflow of the organisation. */
export interface InboxItem {
  id: string;
  workflow_id: string | null;
  workflow_name: string | null;
  thread_id: string;
  canvas_node_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export function useInbox() {
  return useQuery({ queryKey: ["inbox"], queryFn: () => apiFetch<InboxItem[]>("/inbox"), refetchInterval: 5000 });
}
