import { describe, expect, it } from "vitest";
import { listPage, pageNumbers } from "./workflowList";

const NOW = Date.parse("2026-10-29T12:00:00Z");
const ago = (days: number) => new Date(NOW - days * 86_400_000).toISOString();
const ALL = Array.from({ length: 12 }, (_, i) => ({ name: `Flow ${String(i + 1).padStart(2, "0")}`, updated_at: ago(i * 5) }));

describe("listPage", () => {
  it("shows five at a time, newest first, and says which ones", () => {
    const p = listPage(ALL, { q: "", changed: "any", sort: "updated", page: 1, now: NOW });
    expect(p.rows.map((w) => w.name)).toEqual(["Flow 01", "Flow 02", "Flow 03", "Flow 04", "Flow 05"]);
    expect([p.from, p.to, p.total, p.pages]).toEqual([1, 5, 12, 3]);
    const last = listPage(ALL, { q: "", changed: "any", sort: "updated", page: 3, now: NOW });
    expect(last.rows.map((w) => w.name)).toEqual(["Flow 11", "Flow 12"]);
    expect([last.from, last.to]).toEqual([11, 12]);
  });

  it("filters by name and by when it changed, and never leaves an empty page", () => {
    expect(listPage(ALL, { q: "flow 1", changed: "any", sort: "name", page: 1, now: NOW }).rows.map((w) => w.name))
      .toEqual(["Flow 10", "Flow 11", "Flow 12"]);
    const week = listPage(ALL, { q: "", changed: "week", sort: "updated", page: 3, now: NOW });
    expect(week.total).toBe(2); // 0 and 5 days ago
    expect(week.page).toBe(1);
    expect(listPage(ALL, { q: "", changed: "older", sort: "updated", page: 1, now: NOW }).total).toBe(5); // 35 to 55 days ago
    expect(listPage([], { q: "", changed: "any", sort: "updated", page: 1, now: NOW })).toMatchObject({ from: 0, to: 0, pages: 1 });
  });
});

describe("pageNumbers", () => {
  it("lists every page when few, and elides the middle when many", () => {
    expect(pageNumbers(2, 4)).toEqual([1, 2, 3, 4]);
    expect(pageNumbers(1, 20)).toEqual([1, 2, "…", 20]);
    expect(pageNumbers(10, 20)).toEqual([1, "…", 9, 10, 11, "…", 20]);
    expect(pageNumbers(20, 20)).toEqual([1, "…", 19, 20]);
  });
});
