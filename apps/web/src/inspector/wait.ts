/** The Wait block (`core.wait`) in the Inspector: which settings its way of waiting uses, and what a duration or a
 * time typed in means. The parsing mirrors the API's `agentcanvas/waits.py`; the server checks again (E097). */

type Schema = Record<string, unknown>;

export const MODES = { for: "For a while", until: "Until a time", event: "Until an event arrives" } as const;
type Mode = keyof typeof MODES;
const FIELDS: Record<Mode, string[]> = {
  for: ["duration"],
  until: ["until", "timezone"],
  event: ["event", "event_key", "timeout"],
};
const ALL = ["duration", "until", "timezone", "event", "event_key", "timeout"];
const MAX_DAYS = 366;

/** uiSchema for a Wait block's form: only the settings of its way of waiting, the ways in words. */
export function waitUi(config: Record<string, unknown>): Schema {
  const mode = (typeof config.mode === "string" && config.mode in MODES ? config.mode : "for") as Mode;
  const ui: Schema = { mode: { "ui:enumNames": Object.values(MODES) } };
  for (const key of ALL) if (!FIELDS[mode].includes(key)) ui[key] = { "ui:widget": "hidden" };
  return ui;
}

export const isTemplate = (text: string) => text.includes("{{") || text.includes("{%");

const UNITS: Record<string, number> = {
  s: 1, sec: 1, secs: 1, second: 1, seconds: 1,
  m: 60, min: 60, mins: 60, minute: 60, minutes: 60,
  h: 3600, hr: 3600, hrs: 3600, hour: 3600, hours: 3600,
  d: 86400, day: 86400, days: 86400,
  w: 604800, week: 604800, weeks: 604800,
};
const ISO = /^P(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$/;

/** Seconds in a duration (`3d`, `2h 30m`, `PT45M`, `90`), or an error in plain words. */
export function parseDuration(value: string): { seconds: number } | { error: string } {
  const text = value.trim().toLowerCase();
  if (!text) return { error: "no duration given" };
  if (/^\d+(\.\d+)?$/.test(text)) return { seconds: Number(text) };
  if (text.startsWith("p")) {
    const m = ISO.exec(text.toUpperCase());
    if (!m || m.slice(1).every((g) => g === undefined) || text.endsWith("t")) {
      return { error: "not an ISO 8601 duration without years or months, e.g. PT2H or P3D" };
    }
    const [w, d, h, min, s] = m.slice(1).map((g) => Number(g ?? 0));
    return { seconds: w * 604800 + d * 86400 + h * 3600 + min * 60 + s };
  }
  const compact = text.replace(/[\s,]+|\band\b/g, " ").trim();
  const parts = [...compact.matchAll(/(\d+(?:\.\d+)?)\s*([a-z]+)/g)];
  if (!parts.length || compact.replace(/(\d+(?:\.\d+)?)\s*([a-z]+)/g, "").trim()) {
    return { error: "not a duration (e.g. 3d, 2h 30m, 90 minutes or PT45M)" };
  }
  let seconds = 0;
  for (const [, amount, unit] of parts) {
    if (!(unit in UNITS)) return { error: `'${unit}' is not a unit of time (use s, m, h, d or w)` };
    seconds += Number(amount) * UNITS[unit];
  }
  return { seconds };
}

/** "3 days 2 hours": a duration in words, its two largest units. */
export function inWords(seconds: number): string {
  if (seconds < 1) return "no wait";
  const units: [string, number][] = [["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];
  const out: string[] = [];
  let left = Math.round(seconds);
  for (const [name, size] of units) {
    const n = Math.floor(left / size);
    if (n > 0 && out.length < 2) {
      out.push(`${n} ${name}${n > 1 ? "s" : ""}`);
      left -= n * size;
    }
  }
  return out.join(" ");
}

/** What a duration setting says, for the hint under it. */
export function describeDuration(value: unknown): { text: string; ok: boolean } {
  const text = typeof value === "string" ? value : value == null ? "" : String(value);
  if (!text.trim()) return { text: "", ok: true };
  if (isTemplate(text)) return { text: "set when the run gets here", ok: true };
  const found = parseDuration(text);
  if ("error" in found) return { text: found.error, ok: false };
  if (found.seconds > MAX_DAYS * 86400) return { text: `longer than a Wait step may wait (${MAX_DAYS} days)`, ok: false };
  return { text: `waits ${inWords(found.seconds)}`, ok: true };
}

/** What a time setting says: the moment in this browser's time, or why it is not one. A time without an offset is
 * in the step's time zone (UTC when it has none). */
export function describeTime(value: unknown, timezone?: string): { text: string; ok: boolean } {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return { text: "", ok: true };
  if (isTemplate(text)) return { text: "set when the run gets here", ok: true };
  const iso = text.length > 10 ? text.replace(" ", "T") : `${text}T00:00`;
  const offset = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso)) return { text: "not a date and time (e.g. 2026-12-01T09:00)", ok: false };
  if (!offset && timezone && timezone !== "UTC") return { text: `in ${timezone}`, ok: true }; // the server converts it
  const at = new Date(offset ? iso : `${iso}Z`);
  if (Number.isNaN(at.getTime())) return { text: "not a date and time (e.g. 2026-12-01T09:00)", ok: false };
  const shown = at.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return { text: at.getTime() < Date.now() ? `${shown}: already past, so it does not wait` : `until ${shown} your time`, ok: true };
}

/** What a paused Wait step waits for, from its interrupt payload: "until …" or "for the event …". */
export function waitingFor(payload: Record<string, unknown>): string {
  const due = typeof payload.due_at === "string" ? new Date(payload.due_at) : null;
  const when = due && !Number.isNaN(due.getTime()) ? due.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "its time";
  if (typeof payload.event === "string") {
    const key = payload.key ? ` with key ${String(payload.key)}` : "";
    return `for the event ${payload.event}${key}, until ${when} at most`;
  }
  return `until ${when}`;
}
