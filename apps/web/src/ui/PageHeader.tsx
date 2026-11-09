/** The top of a page: where it is (breadcrumbs, when it belongs to a workflow), its title, one sentence on what it is
 * for, and the page's main actions on the right. */
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";

export function PageHeader({ title, description, crumbs, actions }: {
  title: React.ReactNode;
  description?: React.ReactNode;
  crumbs?: { to: string; label: React.ReactNode; testId?: string }[];
  actions?: React.ReactNode;
}) {
  return (
    <div className="page-header">
      {crumbs && crumbs.length > 0 && (
        <nav className="crumbs" aria-label="Breadcrumb">
          {crumbs.map((c) => (
            <span key={c.to}><Link to={c.to} data-testid={c.testId}>{c.label}</Link><ChevronRight size={13} aria-hidden /></span>
          ))}
        </nav>
      )}
      <div className="page-header-row">
        <div>
          <h1>{title}</h1>
          {description && <p className="muted page-description">{description}</p>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
    </div>
  );
}
