// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const loadConversation = vi.fn(async () => ({ thread_id: null as string | null, busy: false, messages: [] as unknown[] }));
const closeConversation = vi.fn(async () => undefined);
const isEmbed = vi.fn(() => false);

vi.mock("./share", async (orig) => ({
  ...(await orig<typeof import("./share")>()),
  tokenFromPath: () => "sh_abc",
  loadShare: vi.fn(async () => info),
  sendMessage,
  uploadShareFile,
  loadConversation,
  closeConversation,
  isEmbed,
}));

const { SharePage } = await import("./SharePage");

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
  sendMessage.mockClear();
  uploadShareFile.mockClear();
  closeConversation.mockClear();
  loadConversation.mockClear();
  isEmbed.mockReturnValue(false);
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

describe("share page history (G-15)", () => {
  const saved = {
    thread_id: "th_9", busy: false,
    messages: [
      { role: "user", text: "Where is my order?", attached: ["receipt.png"] },
      { role: "assistant", text: "It ships Friday.", run_id: "run_9" },
    ],
  };

  it("shows the visitor's conversation again after a reload and continues it", async () => {
    loadConversation.mockResolvedValueOnce(saved);
    render(<SharePage />);
    expect(await screen.findByText("It ships Friday.")).toBeTruthy();
    expect(screen.getByText("Where is my order?")).toBeTruthy();
    expect(screen.getByText(/📎 receipt.png/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "And the invoice?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    expect(sendMessage.mock.calls[0].slice(1, 3)).toEqual(["And the invoice?", "th_9"]); // the same conversation
  });

  it("asks again for an answer a saved conversation was waiting for", async () => {
    const question = { id: "int_1", kind: "input", question: "Which order number?" };
    loadConversation.mockResolvedValueOnce({
      thread_id: "th_9", busy: false, messages: [{ role: "user", text: "Where is it?" }, { role: "assistant", text: question.question, question }],
    });
    render(<SharePage />);
    await screen.findByText("Which order number?");
    expect((screen.getByLabelText("Your answer") as HTMLTextAreaElement).placeholder).toMatch(/Type your answer/);
  });

  it("starts over for good with New conversation", async () => {
    loadConversation.mockResolvedValueOnce(saved);
    render(<SharePage />);
    await screen.findByText("It ships Friday.");
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(closeConversation).toHaveBeenCalledWith("sh_abc");
    expect(screen.queryByText("It ships Friday.")).toBeNull();
  });

  it("does not replace what the visitor is already typing into with a late history", async () => {
    let late!: (v: typeof saved) => void;
    loadConversation.mockReturnValueOnce(new Promise((r) => { late = r; }));
    render(<SharePage />);
    fireEvent.change(await screen.findByLabelText("Your message"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    await act(async () => late(saved));
    expect(screen.queryByText("It ships Friday.")).toBeNull();
  });

  it("goes on without history when it cannot be loaded", async () => {
    loadConversation.mockRejectedValueOnce(new Error("offline"));
    render(<SharePage />);
    expect(await screen.findByLabelText("Your message")).toBeTruthy();
  });
});

describe("share page postMessage API (G-15)", () => {
  const command = (origin: string, text = "Where is my order?") =>
    window.dispatchEvent(new MessageEvent("message", { data: { agentcanvas: 1, type: "prefill", text }, source: window.parent, origin }));

  it("fills the message box on a prefill command from an allowed site only", async () => {
    isEmbed.mockReturnValue(true);
    info.frame_origins = ["https://shop.example"];
    render(<SharePage />);
    const box = (await screen.findByLabelText("Your message")) as HTMLTextAreaElement;
    act(() => command("https://evil.example"));
    expect(box.value).toBe("");
    act(() => command("https://shop.example"));
    expect(box.value).toBe("Where is my order?");
    delete info.frame_origins;
  });

  it("takes any site's prefill when the link lists no sites, but not outside the widget", async () => {
    isEmbed.mockReturnValue(true);
    render(<SharePage />);
    const box = (await screen.findByLabelText("Your message")) as HTMLTextAreaElement;
    act(() => command("https://anywhere.example", "Hi"));
    expect(box.value).toBe("Hi");
    cleanup();
    isEmbed.mockReturnValue(false);
    render(<SharePage />);
    const full = (await screen.findByLabelText("Your message")) as HTMLTextAreaElement;
    act(() => command("https://anywhere.example", "Hi"));
    expect(full.value).toBe(""); // a full page is not framed: it takes no commands
  });
});

describe("share page, a saved message still being answered", () => {
  it("stops checking after a while and lets the visitor stop waiting", async () => {
    const { AWAY_POLL_MS, AWAY_POLLS } = await import("./SharePage");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      loadConversation.mockResolvedValue({ thread_id: "th_1", busy: true, messages: [{ role: "user", text: "hi" }] });
      render(<SharePage />);
      expect(await screen.findByText(/still being answered/)).toBeTruthy();
      await act(async () => { vi.advanceTimersByTime(AWAY_POLL_MS * (AWAY_POLLS + 5)); });
      const calls = loadConversation.mock.calls.length;
      expect(calls).toBeLessThanOrEqual(AWAY_POLLS + 1); // the first load, then the polls; it used to poll for ever
      await act(async () => { vi.advanceTimersByTime(AWAY_POLL_MS * 10); });
      expect(loadConversation.mock.calls.length).toBe(calls);
      fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
      expect(screen.queryByTestId("share-gave-up")).toBeNull();
      expect(screen.queryByText(/still being answered/)).toBeNull();
    } finally {
      vi.useRealTimers();
      loadConversation.mockResolvedValue({ thread_id: null, busy: false, messages: [] });
    }
  });
});
