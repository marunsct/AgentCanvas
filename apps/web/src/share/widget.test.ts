// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CODE from "../../public/widget.js?raw";

const APP = "https://ac.example";

/** Run widget.js as a page's script tag would, and collect what it tells the page. */
function install() {
  const script = document.createElement("script");
  script.setAttribute("src", `${APP}/widget.js`);
  script.setAttribute("data-share", "sh_token123");
  Object.defineProperty(document, "currentScript", { configurable: true, get: () => script });
  new Function(CODE)(); // as the browser runs the script tag
  const heard: { type: string; [k: string]: unknown }[] = [];
  // what the widget posts to the page: jsdom gives such a message no source, unlike the ones delivered below
  const listen = (e: MessageEvent) => { if (e.data?.agentcanvas === 1 && e.source === null) heard.push(e.data); };
  window.addEventListener("message", listen);
  return { heard, stop: () => window.removeEventListener("message", listen) };
}

/** A message as the browser delivers one: with the sender's window and origin (jsdom's own postMessage has neither). */
const deliver = (data: unknown, source: unknown, origin: string) =>
  window.dispatchEvent(new MessageEvent("message", { data, source: source as MessageEventSource, origin }));
const toWidget = (data: unknown) => deliver(data, window, location.origin);
const frame = () => document.querySelector("iframe") as HTMLIFrameElement | null;
const panel = () => document.querySelector("[role=dialog]") as HTMLElement;
const tick = () => new Promise((r) => setTimeout(r, 0));

let page: ReturnType<typeof install>;
beforeEach(() => { document.body.innerHTML = ""; page = install(); });
afterEach(() => { page.stop(); vi.restoreAllMocks(); });

describe("widget.js postMessage API (G-15)", () => {
  it("opens and closes the chat on command and says so", async () => {
    expect(frame()).toBeNull(); // the chat page loads on the first open
    toWidget({ agentcanvas: 1, type: "open" });
    expect(panel().style.display).toBe("block");
    expect(frame()!.src).toBe(`${APP}/s/sh_token123?embed=1`);
    toWidget({ agentcanvas: 1, type: "close" });
    expect(panel().style.display).toBe("none");
    await tick();
    expect(page.heard.map((e) => e.type)).toEqual(["opened", "closed"]);
  });

  it("holds a prefill until the chat page says it is ready, then passes it on", async () => {
    toWidget({ agentcanvas: 1, type: "prefill", text: "Where is my order?" });
    expect(panel().style.display).toBe("block"); // a prefilled chat is one the visitor can see
    const post = vi.spyOn(frame()!.contentWindow!, "postMessage");
    expect(post).not.toHaveBeenCalled();
    deliver({ agentcanvas: 1, type: "ready" }, frame()!.contentWindow, APP);
    expect(post).toHaveBeenCalledWith({ agentcanvas: 1, type: "prefill", text: "Where is my order?" }, APP);
    toWidget({ agentcanvas: 1, type: "prefill", text: "Again" }); // later ones go straight through
    expect(post).toHaveBeenLastCalledWith({ agentcanvas: 1, type: "prefill", text: "Again" }, APP);
    await tick();
    expect(page.heard.map((e) => e.type)).toContain("ready");
  });

  it("tells the page when a reply arrives", async () => {
    toWidget({ agentcanvas: 1, type: "open" });
    deliver({ agentcanvas: 1, type: "reply", text: "It ships Friday.", runId: "run_1", threadId: "th_1", question: false }, frame()!.contentWindow, APP);
    await tick();
    expect(page.heard.find((e) => e.type === "reply")).toEqual({
      agentcanvas: 1, type: "reply", text: "It ships Friday.", runId: "run_1", threadId: "th_1", question: false,
    });
  });

  it("ignores commands that do not come from the page itself", async () => {
    const other = document.createElement("iframe"); // e.g. an ad frame on the same page
    document.body.appendChild(other);
    deliver({ agentcanvas: 1, type: "open" }, other.contentWindow, "https://ads.example");
    deliver({ agentcanvas: 1, type: "open" }, window, "https://evil.example");
    toWidget({ type: "open" }); // not this protocol
    toWidget({ agentcanvas: 1, type: "eval", text: "x" });
    expect(document.querySelector("iframe[src*='/s/sh_']")).toBeNull();
  });

  it("ignores events that do not come from its own chat frame", async () => {
    toWidget({ agentcanvas: 1, type: "open" });
    deliver({ agentcanvas: 1, type: "reply", text: "spoofed" }, window, location.origin);
    deliver({ agentcanvas: 1, type: "reply", text: "spoofed" }, frame()!.contentWindow, "https://evil.example");
    await tick();
    expect(page.heard.filter((e) => e.type === "reply")).toEqual([]);
  });
});

// last: every widget a test installs keeps listening to the window, so one left open would answer later commands
describe("widget.js label (EU AI Act Art. 50)", () => {
  it("names the button an AI assistant unless the site names it otherwise", async () => {
    const button = document.querySelector("[data-agentcanvas-widget] button") as HTMLButtonElement;
    expect(button.getAttribute("aria-label")).toBe("Open AI assistant");
    expect(button.title).toBe("AI assistant");
    toWidget({ agentcanvas: 1, type: "open" });
    expect(frame()!.title).toBe("AI assistant");
    await tick();
  });
});
