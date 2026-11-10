/** The frame around every page except the editor (which has its own bar and borrows `PersonMenu`): one top bar with
 * the project, the product's pages, Settings and the person's menu, so moving between pages never goes through Home. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { Activity, BookOpen, Brain, ChevronDown, FolderCode, Inbox, LayoutGrid, LifeBuoy, LogOut, Plus, Radio, Settings, Sparkles, User } from "lucide-react";
import { Suspense } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { maturaMode, signInEnabled, signOut, switchOrganisation } from "../api/auth";
import { apiFetch } from "../api/client";
import { PROJECT_ID, PROJECT_NAME, switchProject, useInbox, WORKSPACE_ID, WORKSPACES, chooseWorkspace } from "../api/queries";
import { useCan, useMe } from "../api/session";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import { promptDialog } from "../ui/dialog";
import { Menu } from "../ui/Menu";
import { ErrorBoundary } from "../ui/QueryStatus";

const ICON = { size: 15, strokeWidth: 1.8, "aria-hidden": true } as const;

export function AppShell() {
  return (
    <>
      <TopBar />
      {/* a page that crashes keeps the bar above it, so another page is one click away */}
      <RouteBoundary label="This page">
        <Suspense fallback={<div className="page muted" role="status">Loading…</div>}><Outlet /></Suspense>
      </RouteBoundary>
    </>
  );
}

/** An error boundary that starts afresh on every page: one crash used to stay on screen after moving to another. */
export function RouteBoundary({ label, children }: { label: string; children: React.ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary resetKey={pathname} label={label}>{children}</ErrorBoundary>;
}

function TopBar() {
  const canBuild = useCan("builder");
  const pending = useInbox().data?.length ?? 0;
  const link = (to: string, label: string, icon: React.ReactNode, testId?: string, end = false) => (
    <NavLink to={to} end={end} data-testid={testId} title={label} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
      {icon}<span className="nav-label">{label}</span>
    </NavLink>
  );
  return (
    <header className="topbar app-bar">
      <NavLink to="/" className="brand" aria-label="AgentCanvas home">AgentCanvas</NavLink>
      <ProjectSwitcher />
      <nav className="nav-links" aria-label="Pages">
        {link("/", "Workflows", <LayoutGrid {...ICON} />, "workflows-link", true)}
        {canBuild && link("/playground", "Playground", <Sparkles {...ICON} />, "playground-link")}
        {link("/runs", "Runs", <Activity {...ICON} />, "runs-link")}
        <NavLink to="/inbox" data-testid="inbox-link" title="Inbox" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <Inbox {...ICON} /><span className="nav-label">Inbox</span>{pending > 0 && <span className="badge warn" aria-label={`${pending} waiting`}>{pending}</span>}
        </NavLink>
        {link("/knowledge", "Knowledge", <BookOpen {...ICON} />, "knowledge-link")}
        {link("/memory", "Memory", <Brain {...ICON} />, "memory-link")}
        {link("/workspace", "Workspace", <FolderCode {...ICON} />, "workspace-link")}
        {link("/events", "Events", <Radio {...ICON} />, "events-link")}
      </nav>
      <span className="spacer" />
      {link("/settings", "Settings", <Settings {...ICON} />, "settings-link")}
      <PersonMenu />
    </header>
  );
}

/** The project the app works in: switch to another, or (builders) make a new one. Switching reloads the app, since
 * every page reads the project chosen at start-up. */
function ProjectSwitcher() {
  const canBuild = useCan("builder");
  const projects = useQuery({ queryKey: ["projects", WORKSPACE_ID], queryFn: () =>
    apiFetch<{ id: string; name: string }[]>(WORKSPACE_ID ? `/projects?workspace_id=${encodeURIComponent(WORKSPACE_ID)}` : "/projects") });
  const create = useMutation({
    mutationFn: (name: string) => apiFetch<{ id: string }>("/projects", { method: "POST", json: { name, workspace_id: WORKSPACE_ID } }),
    onSuccess: (p) => switchProject(p.id),
  });
  const pickWorkspace = useMutation({ mutationFn: (id: string | null) => chooseWorkspace(id), onSuccess: () => location.assign("/") });
  return (
    <Menu align="left" className="project-switcher" testId="project-switcher" title="Switch project"
      label={<><span className="crumb" data-testid="project-name">{PROJECT_NAME}</span><ChevronDown {...ICON} size={13} /></>}>
      {(close) => (
        <>
          <p className="menu-heading">Projects</p>
          {projects.data?.map((p) => (
            <button key={p.id} type="button" role="menuitemradio" aria-checked={p.id === PROJECT_ID}
              className={p.id === PROJECT_ID ? "active" : ""} onClick={() => { close(); if (p.id !== PROJECT_ID) switchProject(p.id); }}>
              {p.name}
            </button>
          ))}
          {projects.isError && <p className="error-text small">Could not list the projects: {(projects.error as Error).message}</p>}
          {canBuild && (
            <button type="button" role="menuitem" data-testid="new-project" onClick={async () => {
              close();
              const name = (await promptDialog({ title: "New project", body: "A project holds its own workflows, knowledge, secrets and settings.",
                confirm: "Create", input: { label: "Name", required: true } }))?.trim();
              if (name) create.mutate(name);
            }}><Plus {...ICON} /> New project…</button>
          )}
          {create.error && <p className="error-text small" role="alert">{(create.error as Error).message}</p>}
          {WORKSPACES.length > 0 && (
            <>
              <hr />
              <p className="menu-heading">Workspace</p>
              <select aria-label="workspace" data-testid="workspace-switcher" value={WORKSPACE_ID ?? ""} disabled={pickWorkspace.isPending}
                onChange={(e) => pickWorkspace.mutate(e.target.value || null)}>
                <option value="">All workspaces</option>
                {WORKSPACES.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
              {pickWorkspace.error && <p className="error-text small" role="alert">{(pickWorkspace.error as Error).message}</p>}
            </>
          )}
        </>
      )}
    </Menu>
  );
}

/** Who is signed in: their role, the organisation (and switching it in Matura mode), the theme, signing out. */
export function PersonMenu() {
  const me = useMe((s) => s.me);
  const orgs = Object.keys(me.memberships);
  const initial = me.email ? me.email.slice(0, 1).toUpperCase() : <User {...ICON} />;
  return (
    <Menu testId="me" className="person-menu" title={me.email || "you"}
      label={<span className="avatar" aria-label={`${me.email || "you"}, ${me.role}`}>{initial}</span>}>
      {(close) => (
        <>
          <div className="menu-person">
            <b>{me.email || "Local developer"}</b>
            <span className="muted small" data-testid="me-role">{me.role}{me.org_names?.[me.org_id] ? ` · ${me.org_names[me.org_id]}` : ""}
              {me.project_role && me.project_role !== me.role && <> · {me.project_role} in this project</>}</span>
          </div>
          {maturaMode() && orgs.length > 1 && (
            <>
              <hr />
              <p className="menu-heading">Organisation</p>
              <select aria-label="organisation" data-testid="org-switcher" value={me.org_id}
                onChange={(e) => void switchOrganisation(e.target.value)}>
                {orgs.map((id) => <option key={id} value={id}>{me.org_names?.[id] ?? id}</option>)}
              </select>
            </>
          )}
          <hr />
          <p className="menu-heading">Theme</p>
          <ThemeSwitcher />
          <hr />
          <NavLink to="/guide" role="menuitem" data-testid="guide-link" onClick={close}><LifeBuoy {...ICON} /> User guide</NavLink>
          {signInEnabled() && (
            <>
              <hr />
              <button type="button" role="menuitem" onClick={() => void signOut()}><LogOut {...ICON} /> Sign out</button>
            </>
          )}
        </>
      )}
    </Menu>
  );
}
