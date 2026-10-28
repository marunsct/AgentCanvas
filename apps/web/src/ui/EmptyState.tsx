/** What an empty list says: an icon, one sentence on what goes here, and the action that fills it. */
import type { LucideIcon } from "lucide-react";

export function EmptyState({ icon: Icon, title, children, action, testId }: {
  icon: LucideIcon; title: string; children?: React.ReactNode; action?: React.ReactNode; testId?: string;
}) {
  return (
    <div className="empty" data-testid={testId}>
      <Icon size={28} strokeWidth={1.5} aria-hidden />
      <p className="empty-title">{title}</p>
      {children && <p className="muted small">{children}</p>}
      {action}
    </div>
  );
}
