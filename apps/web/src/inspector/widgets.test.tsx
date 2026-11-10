// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import type { WidgetProps } from "@rjsf/utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Check = { ok: boolean; error?: string | null };
const calls: { source: string; resolve: (r: Check) => void; signal?: AbortSignal | null }[] = [];
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn((_path: string, init: { json: { source: string }; signal?: AbortSignal }) =>
    new Promise<Check>((resolve) => { calls.push({ source: init.json.source, resolve, signal: init.signal }); })),
}));

const { CelWidget } = await import("./widgets");

const props = (value: string) => ({ id: "cel", value, onChange: () => undefined }) as unknown as WidgetProps;

beforeEach(() => { calls.length = 0; vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("CEL expression check", () => {
  it("shows the verdict on the current text when answers arrive out of order", async () => {
    const { container, rerender } = render(<CelWidget {...props("state.x ==")} />);
    act(() => { vi.advanceTimersByTime(300); });
    rerender(<CelWidget {...props("state.x == 1")} />);
    act(() => { vi.advanceTimersByTime(300); });
    expect(calls.map((c) => c.source)).toEqual(["state.x ==", "state.x == 1"]);
    expect(calls[0].signal?.aborted).toBe(true); // the older check is not waited for
    await act(async () => { calls[1].resolve({ ok: true }); });
    await act(async () => { calls[0].resolve({ ok: false, error: "unexpected end" }); });
    expect(container.querySelector(".field-error")).toBeNull();
  });

  it("still shows the error for the text being edited", async () => {
    const { container } = render(<CelWidget {...props("state.x ==")} />);
    act(() => { vi.advanceTimersByTime(300); });
    await act(async () => { calls[0].resolve({ ok: false, error: "unexpected end" }); });
    expect(container.querySelector(".field-error")?.textContent).toBe("unexpected end");
  });
});
