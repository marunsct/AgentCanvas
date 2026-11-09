// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmDialog, DialogHost, promptDialog, toast } from "./dialog";

afterEach(cleanup);

describe("confirmDialog", () => {
  it("answers true on confirm and false on cancel, Escape or a click outside", async () => {
    render(<DialogHost />);
    for (const how of ["confirm", "cancel", "escape", "outside"] as const) {
      let answer: Promise<boolean> = Promise.resolve(false);
      act(() => { answer = confirmDialog({ title: "Delete it?", confirm: "Delete", danger: true }); });
      const dialog = screen.getByRole("dialog", { name: "Delete it?" });
      // focus goes to the dialog, on the least destructive choice: a stray or repeated Enter cancels
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
      act(() => {
        if (how === "confirm") screen.getByRole("button", { name: "Delete" }).click();
        if (how === "cancel") screen.getByRole("button", { name: "Cancel" }).click();
        if (how === "escape") fireEvent.keyDown(dialog, { key: "Escape" });
        if (how === "outside") fireEvent.mouseDown(dialog.parentElement!);
      });
      expect(await answer).toBe(how === "confirm");
      expect(screen.queryByRole("dialog")).toBeNull();
    }
  });

  it("shows one dialog at a time, in order", async () => {
    render(<DialogHost />);
    let a: Promise<boolean> = Promise.resolve(false), b: Promise<boolean> = Promise.resolve(false);
    act(() => { a = confirmDialog({ title: "First" }); b = confirmDialog({ title: "Second" }); });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "OK" })); // not destructive: Enter confirms
    act(() => screen.getByRole("button", { name: "OK" }).click());
    expect(await a).toBe(true);
    expect(screen.getByRole("dialog", { name: "Second" })).toBeTruthy();
    act(() => screen.getByRole("button", { name: "Cancel" }).click());
    expect(await b).toBe(false);
  });
});

describe("promptDialog", () => {
  it("answers the text entered, or null when cancelled; a required field cannot be confirmed empty", async () => {
    render(<DialogHost />);
    let answer: Promise<string | null> = Promise.resolve(null);
    act(() => { answer = promptDialog({ title: "Frame label", confirm: "Save", input: { value: "Group", required: true } }); });
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.value).toBe("Group");
    act(() => { fireEvent.change(box, { target: { value: "  " } }); });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    act(() => { fireEvent.change(box, { target: { value: "Intake" } }); });
    act(() => { fireEvent.submit(box.form!); }); // Enter in the field
    expect(await answer).toBe("Intake");

    act(() => { answer = promptDialog({ title: "Reason", input: {} }); });
    act(() => screen.getByRole("button", { name: "Cancel" }).click());
    expect(await answer).toBeNull();
  });
});

describe("toast", () => {
  it("shows a notice that goes away by itself", () => {
    vi.useFakeTimers();
    try {
      render(<DialogHost />);
      act(() => toast("Saved"));
      expect(screen.getByTestId("toasts").textContent).toBe("Saved");
      act(() => { vi.advanceTimersByTime(4100); });
      expect(screen.getByTestId("toasts").textContent).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });
});
