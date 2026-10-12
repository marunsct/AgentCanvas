/** Budget settings helpers (S26). */

/** The most alerts a budget can have (the IR's `alert_at` holds at most 5). */
export const MAX_ALERTS = 5;

/** "50, 80" (percent of the day or month limit) -> [0.5, 0.8]; empty -> null (the default, 80 %); invalid (a value
 * outside 1-100, or more than 5 different values) -> undefined. */
export function parseAlertShares(raw: string): number[] | null | undefined {
  if (raw.trim() === "") return null;
  const shares = raw.split(/[,\s%]+/).filter(Boolean).map(Number);
  if (shares.some((p) => !(p > 0 && p <= 100))) return undefined;
  const unique = [...new Set(shares)];
  if (unique.length > MAX_ALERTS) return undefined;
  return unique.sort((a, b) => a - b).map((p) => p / 100);
}
