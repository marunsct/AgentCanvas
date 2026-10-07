/** "What went wrong / how to fix it" line under an error. */
import type { Help } from "./diagnostics";

export function HelpText({ help, className = "diag-help" }: { help: Help | undefined; className?: string }) {
  if (!help) return null;
  return (
    <span className={className} data-testid="error-help">
      <b>What happened:</b> {help.what} <b>How to fix:</b> {help.fix}
    </span>
  );
}
