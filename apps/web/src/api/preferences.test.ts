// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("./client", () => ({ apiFetch }));

const { applyPreferences } = await import("./preferences");
const { useUi } = await import("../editor/ui");

describe("preferences that follow the person", () => {
  beforeEach(() => { apiFetch.mockClear(); localStorage.clear(); useUi.setState({ simple: false }); });

  it("saves the editor's Simple or Full mode with the person", () => {
    useUi.getState().setSimple(true);
    expect(apiFetch).toHaveBeenCalledWith("/me/preferences", { method: "PUT", json: { simple_mode: true } });
    expect(localStorage.getItem("agentcanvas.simple")).toBe("1"); // and keeps it in this browser
  });

  it("opens the editor in the mode saved on another browser, without saving it back", async () => {
    await applyPreferences({ simple_mode: true });
    expect(useUi.getState().simple).toBe(true);
    await applyPreferences({ simple_mode: false });
    expect(useUi.getState().simple).toBe(false);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("leaves this browser's mode alone when the person never chose one", async () => {
    useUi.setState({ simple: true });
    await applyPreferences({});
    expect(useUi.getState().simple).toBe(true);
  });
});
