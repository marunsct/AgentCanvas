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
import { ProjectSettings } from "./ProjectSettings";
import { SandboxSettings } from "./SandboxSettings";
import { SecretsSettings } from "./SecretsSettings";

const ModelSettings = lazy(() => import("../app/SetupPage").then((m) => ({ default: m.SetupPage })));
const MembersPage = lazy(() => import("../app/MembersPage").then((m) => ({ default: m.MembersPage })));
const PoliciesPage = lazy(() => import("../app/PoliciesPage").then((m) => ({ default: m.PoliciesPage })));
const AuditPage = lazy(() => import("../app/AuditPage").then((m) => ({ default: m.AuditPage })));

interface Section { id: string; group: "Project" | "Organisation" | "You"; label: string; read?: Role; render: () => React.ReactNode }

export const SECTIONS: Section[] = [
  { id: "general", group: "Project", label: "General", render: () => <ProjectSettings /> },
  { id: "models", group: "Project", label: "Models", render: () => <ModelSettings /> },
  { id: "copilot", group: "Project", label: "Copilot", render: () => <CopilotSettings /> },
  { id: "secrets", group: "Project", label: "Secrets", render: () => <SecretsSettings /> },
  { id: "sandbox", group: "Project", label: "Sandbox", render: () => <SandboxSettings /> },
  { id: "observability", group: "Project", label: "Observability", render: () => <TraceExportSettings /> },
  { id: "members", group: "Organisation", label: "Members", render: () => <MembersPage /> },
  { id: "policies", group: "Organisation", label: "Policies", render: () => <PoliciesPage /> },
  { id: "audit", group: "Organisation", label: "Audit log", read: "admin", render: () => <AuditPage /> },
  { id: "appearance", group: "You", label: "Appearance", render: () => <Appearance /> },
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
