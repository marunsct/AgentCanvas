/** Budget settings helpers (S26). */

/** "50, 80" (percent of the day or month limit) -> [0.5, 0.8]; empty -> null (the default, 80 %); invalid -> undefined. */
export function parseAlertShares(raw: string): number[] | null | undefined {
  if (raw.trim() === "") return null;
  const shares = raw.split(/[,\s%]+/).filter(Boolean).map(Number);
  if (shares.some((p) => !(p > 0 && p <= 100))) return undefined;
  return [...new Set(shares)].sort((a, b) => a - b).map((p) => p / 100);
}
