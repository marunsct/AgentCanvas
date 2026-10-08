/** Plain-English "what went wrong / how to fix it" for every validation code and for common run errors. Pure. */
import type { Diagnostic } from "../api/types";
import catalog from "./diagnostics.generated.json";

export interface Help {
  what: string; // what went wrong, without jargon
  fix: string; // what to click or change
}

/** One entry per validation code; generated from apps/api/agentcanvas/validation/help.json (`pnpm gen:help`), which
 * the Copilot uses too. The server message adds the specifics. */
export const DIAGNOSTIC_HELP: Record<string, Help> = catalog.diagnostics;

export function diagnosticHelp(d: Pick<Diagnostic, "code">): Help | undefined {
  return DIAGNOSTIC_HELP[d.code];
}

/** Common run failures (model providers, local servers, templates…) explained, with a fix. */
const RUN_ERRORS: [RegExp, Help][] = catalog.run_errors.map((r) => [new RegExp(r.pattern, "i"), { what: r.what, fix: r.fix }]);

export function runErrorHelp(message: string | null | undefined): Help | undefined {
  if (!message) return undefined;
  return RUN_ERRORS.find(([re]) => re.test(message))?.[1];
}
