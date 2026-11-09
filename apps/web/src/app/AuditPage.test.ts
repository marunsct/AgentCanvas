import { describe, expect, it } from "vitest";
import { actionLabel, toCsv } from "./AuditPage";

describe("audit log", () => {
  it("names actions in words and exports CSV with quoting", () => {
    expect(actionLabel("put_secret")).toBe("Put secret");
    const csv = toCsv([{ id: 1, created_at: "2026-10-28T10:00:00Z", actor_id: "usr_1", actor_email: "a@b.c", action: "publish",
      target: 'wf_id=wf_1, "x"', detail: { reason: "demo" }, ip: "127.0.0.1" }]);
    expect(csv.split("\n")[0]).toBe("time,who,action,target,ip,detail");
    expect(csv.split("\n")[1]).toBe('2026-10-28T10:00:00Z,a@b.c,publish,"wf_id=wf_1, ""x""",127.0.0.1,"{""reason"":""demo""}"');
  });
});
