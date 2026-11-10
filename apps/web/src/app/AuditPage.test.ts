import { describe, expect, it } from "vitest";
import { actionLabel, chainSummary, toCsv } from "./AuditPage";

describe("audit log", () => {
  it("names actions in words and exports CSV with quoting", () => {
    expect(actionLabel("put_secret")).toBe("Put secret");
    const csv = toCsv([{ id: 1, created_at: "2026-10-28T10:00:00Z", actor_id: "usr_1", actor_email: "a@b.c", action: "publish",
      target: 'wf_id=wf_1, "x"', detail: { reason: "demo" }, ip: "127.0.0.1" }]);
    expect(csv.split("\n")[0]).toBe("time,who,action,target,ip,detail");
    expect(csv.split("\n")[1]).toBe('2026-10-28T10:00:00Z,a@b.c,publish,"wf_id=wf_1, ""x""",127.0.0.1,"{""reason"":""demo""}"');
  });

  it("sums up the hash chain check", () => {
    const base = { checked: 3, unchained: 0, head_hash: "ab", problems: [] };
    expect(chainSummary({ ...base, ok: true })).toBe("Intact: 3 entries checked, none changed or missing.");
    expect(chainSummary({ ...base, ok: true, checked: 1, unchained: 2 })).toBe(
      "Intact: 1 entry checked, none changed or missing; 2 older entries were written before the chain existed.");
    const broken = chainSummary({ ...base, ok: false, problems: [{ id: 7, kind: "changed", message: "edited" }] });
    expect(broken).toContain("1 entry does not match");
    expect(broken).toContain("security incident");
  });
});
