/** The home page's workflow list as data: find by name, filter by when it changed, sort, and cut into pages. */
export const PAGE_SIZE = 5;
export type Sort = "updated" | "name";
export type Changed = "any" | "week" | "month" | "older";
export const CHANGED: { id: Changed; label: string }[] = [
  { id: "any", label: "Any time" },
  { id: "week", label: "Last 7 days" },
  { id: "month", label: "Last 30 days" },
  { id: "older", label: "Older than 30 days" },
];

type Row = { name: string; updated_at: string };
const DAY = 86_400_000;

export function listPage<T extends Row>(all: T[], { q, changed, sort, page, now = Date.now() }: {
  q: string; changed: Changed; sort: Sort; page: number; now?: number;
}) {
  const needle = q.trim().toLowerCase();
  const age = (w: T) => now - new Date(w.updated_at).getTime();
  const found = all.filter((w) => (!needle || w.name.toLowerCase().includes(needle))
    && (changed === "any" || (changed === "week" ? age(w) <= 7 * DAY : changed === "month" ? age(w) <= 30 * DAY : age(w) > 30 * DAY)));
  const sorted = [...found].sort((a, b) => (sort === "name" ? a.name.localeCompare(b.name)
    : new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()));
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const current = Math.min(Math.max(1, page), pages); // a filter that shrinks the list never leaves an empty page
  const start = (current - 1) * PAGE_SIZE;
  return { rows: sorted.slice(start, start + PAGE_SIZE), page: current, pages, total: sorted.length,
    from: sorted.length ? start + 1 : 0, to: Math.min(start + PAGE_SIZE, sorted.length) };
}

/** Page numbers to show: all of them when few, else the first, the last and the ones around the current page. */
export function pageNumbers(page: number, pages: number): (number | "…")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const near = [page - 1, page, page + 1].filter((n) => n > 1 && n < pages);
  const out: (number | "…")[] = [1];
  if (near[0] > 2) out.push("…");
  out.push(...near);
  if (near[near.length - 1] < pages - 1) out.push("…");
  out.push(pages);
  return out;
}
