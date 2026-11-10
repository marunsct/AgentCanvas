import { describe, expect, it } from "vitest";
import { describeDuration, describeTime, inWords, parseDuration, waitUi, waitingFor } from "./wait";

describe("Wait block settings", () => {
  it("reads durations the way the server does", () => {
    const cases: [string, number][] = [
      ["3d", 259200], ["2h 30m", 9000], ["1h30m", 5400], ["90 minutes", 5400], ["1 week", 604800],
      ["2 hours and 5 minutes", 7500], ["PT45M", 2700], ["P1DT2H", 93600], ["45", 45], ["1.5h", 5400],
    ];
    for (const [text, seconds] of cases) expect(parseDuration(text)).toEqual({ seconds });
    for (const bad of ["soon", "3 fortnights", "P1Y", "PT", "P1DT", "3d later"]) expect(parseDuration(bad)).toHaveProperty("error");
  });

  it("says what a setting means under it", () => {
    expect(describeDuration("2h 30m")).toEqual({ text: "waits 2 hours 30 minutes", ok: true });
    expect(describeDuration("{{ state.delay }}")).toEqual({ text: "set when the run gets here", ok: true });
    expect(describeDuration("400d").ok).toBe(false);
    expect(describeDuration("soon").ok).toBe(false);
    expect(describeTime("2020-01-01T00:00Z").text).toContain("already past");
    expect(describeTime("2999-01-01T09:00", "Europe/Berlin")).toEqual({ text: "in Europe/Berlin", ok: true });
    expect(describeTime("next week").ok).toBe(false);
    expect(inWords(93600)).toBe("1 day 2 hours");
  });

  it("shows only the settings of the way it waits", () => {
    expect(waitUi({})).toMatchObject({ until: { "ui:widget": "hidden" }, event: { "ui:widget": "hidden" } });
    expect(waitUi({})).not.toHaveProperty("duration");
    const event = waitUi({ mode: "event" });
    expect(event).toMatchObject({ duration: { "ui:widget": "hidden" }, until: { "ui:widget": "hidden" } });
    expect(event).not.toHaveProperty("timeout");
    expect(event).not.toHaveProperty("event_key");
    expect(waitUi({ mode: "until" })).not.toHaveProperty("timezone");
  });

  it("says what a paused step waits for", () => {
    expect(waitingFor({ due_at: "not a time" })).toBe("until its time");
    expect(waitingFor({ event: "a.b", key: "7", due_at: "2030-01-01T00:00:00Z" })).toMatch(/^for the event a\.b with key 7, until .* at most$/);
  });
});
