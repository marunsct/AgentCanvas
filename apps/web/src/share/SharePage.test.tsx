// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ShareInfo } from "./share";

const info: ShareInfo = {
  title: "Looks", greeting: "Send a picture.", suggestions: [], input: { field: "question", chat: false },
  attachments: { field: "photo", accept: "image/*", multiple: false },
};
const uploaded = { kind: "file" as const, id: "fil_1", name: "me.png", media_type: "image/png", size: 2048 };

const sendMessage = vi.fn(async (_t: string, _m: string, _th: string | null, on: (e: unknown) => void) => {
  on({ type: "RUN_STARTED", runId: "run_1", threadId: "th_1" });
  on({ type: "RUN_FINISHED", runId: "run_1", threadId: "th_1", status: "success", answer: "A face.", interrupts: [] });
});
const uploadShareFile = vi.fn(async () => uploaded);

vi.mock("./share", async (orig) => ({
  ...(await orig<typeof import("./share")>()),
  tokenFromPath: () => "sh_abc",
  loadShare: vi.fn(async () => info),
  sendMessage,
  uploadShareFile,
}));

const { SharePage } = await import("./SharePage");

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
  sendMessage.mockClear();
  uploadShareFile.mockClear();
});
afterEach(cleanup);

describe("share page attachments (G-5)", () => {
  it("lets a visitor attach an image, shows its preview and sends it with the message", async () => {
    render(<SharePage />);
    const picker = await screen.findByLabelText("photo file");
    fireEvent.change(picker, { target: { files: [new File([new Uint8Array(2048)], "me.png", { type: "image/png" })] } });
    expect(await screen.findByAltText("Preview of me.png")).toBeTruthy();
    expect(uploadShareFile).toHaveBeenCalledWith("sh_abc", expect.any(File));

    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "who is this?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    expect(sendMessage.mock.calls[0].slice(1, 3)).toEqual(["who is this?", null]);
    expect((sendMessage.mock.calls[0] as unknown[])[5]).toEqual([uploaded]);
    expect(await screen.findByText("A face.")).toBeTruthy();
    expect(screen.getByText(/📎 me.png/)).toBeTruthy();
    expect(screen.queryByAltText("Preview of me.png")).toBeNull(); // sent: the picker is empty again
  });

  it("can send a file without a message", async () => {
    render(<SharePage />);
    const send = (await screen.findByRole("button", { name: "Send" })) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("photo file"), { target: { files: [new File([new Uint8Array(10)], "me.png", { type: "image/png" })] } });
    await screen.findByAltText("Preview of me.png");
    expect(send.disabled).toBe(false);
  });
});
