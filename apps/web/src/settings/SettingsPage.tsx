/** Settings: one place for everything that is configured rather than built, by scope: this project, the organisation,
 * and the person. A section the person's role cannot change is shown read-only with a sentence saying so; a section
 * whose data their role cannot read is not listed. Old addresses (/setup, /members, /audit, /policies) lead here. */
import { lazy, Suspense } from "react";
import { Navigate, NavLink, useParams } from "react-router";
import { PROJECT_NAME } from "../api/queries";
import { useCan, useMe, type Role } from "../api/session";
import { CopilotSettings } from "../copilot/CopilotSettings";
import { TraceExportSettings } from "../observability/TraceExportSettings";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import { PageHeader } from "../ui/PageHeader";
import { ModelDefaults } from "./ModelDefaults";
import { OrgLimits } from "./OrgLimits";
import { OrgSettings } from "./OrgSettings";
import { KnowledgeSettings } from "./KnowledgeSettings";
import { ProjectSettings } from "./ProjectSettings";
import { SandboxSettings } from "./SandboxSettings";
import { SecretsSettings } from "./SecretsSettings";
import { ServerSettings } from "./ServerSettings";
import { SpendingSettings } from "./SpendingSettings";

const ModelSettings = lazy(() => import("../app/SetupPage").then((m) => ({ default: m.SetupPage })));
const MembersPage = lazy(() => import("../app/MembersPage").then((m) => ({ default: m.MembersPage })));
const PoliciesPage = lazy(() => import("../app/PoliciesPage").then((m) => ({ default: m.PoliciesPage })));
const AuditPage = lazy(() => import("../app/AuditPage").then((m) => ({ default: m.AuditPage })));

interface Section { id: string; group: "Project" | "Organisation" | "You"; label: string; read?: Role; render: () => React.ReactNode }

export const SECTIONS: Section[] = [
  { id: "general", group: "Project", label: "General", render: () => <ProjectSettings /> },
  { id: "models", group: "Project", label: "Models", render: () => <><ModelSettings /><ModelDefaults /></> },
  { id: "copilot", group: "Project", label: "Copilot", render: () => <CopilotSettings /> },
  { id: "secrets", group: "Project", label: "Secrets", render: () => <SecretsSettings /> },
  { id: "sandbox", group: "Project", label: "Sandbox", render: () => <SandboxSettings /> },
  { id: "knowledge", group: "Project", label: "Knowledge", render: () => <KnowledgeSettings /> },
  { id: "observability", group: "Project", label: "Observability", render: () => <TraceExportSettings /> },
  { id: "organisation", group: "Organisation", label: "General", render: () => <><OrgSettings /><OrgLimits /></> },
  { id: "members", group: "Organisation", label: "Members", render: () => <MembersPage /> },
  { id: "policies", group: "Organisation", label: "Policies", render: () => <PoliciesPage /> },
  { id: "spending", group: "Organisation", label: "Spending", read: "admin", render: () => <SpendingSettings /> },
  { id: "audit", group: "Organisation", label: "Audit log", read: "admin", render: () => <AuditPage /> },
  { id: "server", group: "Organisation", label: "Server", read: "admin", render: () => <ServerSettings /> },
  { id: "appearance", group: "You", label: "Appearance", render: () => <Appearance /> },
  { id: "shortcuts", group: "You", label: "Keyboard shortcuts", render: () => <Shortcuts /> },
];

export function SettingsPage() {
  const { section } = useParams();
  const admin = useCan("admin");
  const org = useMe((s) => s.me.org_names?.[s.me.org_id]);
  const visible = SECTIONS.filter((s) => s.read !== "admin" || admin);
  const current = visible.find((s) => s.id === section);
  if (!current) return <Navigate to={`/settings/${visible[0].id}`} replace />;
  const groups = { Project: PROJECT_NAME, Organisation: org ?? "This organisation", You: "Only you" } as const;
  return (
    <div className="page settings-page">
      <PageHeader title="Settings" />
      <div className="settings-layout">
        <nav className="settings-rail" aria-label="Settings sections">
          {(Object.keys(groups) as Section["group"][]).map((g) => (
            <div key={g} className="rail-group">
              <p className="rail-heading">{g}<small>{groups[g]}</small></p>
              {visible.filter((s) => s.group === g).map((s) => (
                <NavLink key={s.id} to={`/settings/${s.id}`} data-testid={`settings-${s.id}`}
                  className={({ isActive }) => (isActive ? "active" : "")}>{s.label}</NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="settings-content" data-testid={`settings-section-${current.id}`}>
          <Suspense fallback={<p className="muted" role="status">Loading…</p>}>{current.render()}</Suspense>
        </div>
      </div>
    </div>
  );
}

function Appearance() {
  return (
    <section className="settings-card" aria-label="Appearance">
      <h2>Appearance</h2>
      <p className="muted small">The colour theme of this browser. System follows your computer's light or dark setting.</p>
      <ThemeSwitcher />
    </section>
  );
}

const MOD = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";
const SHORTCUTS: [string, string][] = [
  [`${MOD} K`, "Command palette: add a block, run, open the Copilot, or type a request for it"],
  [`${MOD} Z`, "Undo (a Copilot proposal you accepted is one step)"],
  [`${MOD} Shift Z`, "Redo"],
  [`${MOD} C / ${MOD} V`, "Copy and paste the selected blocks"],
  [`${MOD} G`, "Turn the selected steps into a subgraph"],
  ["Delete / Backspace", "Delete the selected blocks or wires"],
  ["Enter or Space", "Add the focused block from the palette; open a port's menu"],
  ["Tab / Shift Tab", "Move between the canvas, the palette and the panels"],
  ["Escape", "Close a menu, a dialog or the command palette"],
  ["Enter", "Send a message to the Copilot (Shift Enter for a new line)"],
];

function Shortcuts() {
  return (
    <section className="settings-card" aria-label="Keyboard shortcuts">
      <h2>Keyboard shortcuts</h2>
      <p className="muted small">In the workflow editor. Shortcuts do nothing while you type in a field.</p>
      <table className="table">
        <tbody>{SHORTCUTS.map(([k, what]) => <tr key={k}><td style={{ width: "34%" }}><kbd>{k}</kbd></td><td>{what}</td></tr>)}</tbody>
      </table>
    </section>
  );
}
