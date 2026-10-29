// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_PREFILL, originAllowed, parseCommand, postToParent } from "./embedApi";

afterEach(() => vi.restoreAllMocks());

describe("originAllowed", () => {
  it("allows any site when the link lists none", () => {
    expect(originAllowed("https://anywhere.example", [])).toBe(true);
    expect(originAllowed("https://anywhere.example", undefined)).toBe(true);
  });

  it("allows exactly the listed sites and their wildcard subdomains", () => {
    const allowed = ["https://www.example.com", "https://*.shop.example", "http://localhost:3000"];
    expect(originAllowed("https://www.example.com", allowed)).toBe(true);
    expect(originAllowed("https://WWW.example.com", allowed)).toBe(false); // a browser sends lower case
    expect(originAllowed("https://example.com", allowed)).toBe(false);
    expect(originAllowed("https://a.shop.example", allowed)).toBe(true);
    expect(originAllowed("https://a.b.shop.example", allowed)).toBe(true);
    expect(originAllowed("https://shop.example", allowed)).toBe(false); // the wildcard is for subdomains
    expect(originAllowed("http://a.shop.example", allowed)).toBe(false); // and for the listed scheme
    expect(originAllowed("https://evilshop.example", allowed)).toBe(false);
    expect(originAllowed("https://a.shop.example.evil.com", allowed)).toBe(false);
    expect(originAllowed("http://localhost:3000", allowed)).toBe(true);
    expect(originAllowed("http://localhost:3001", allowed)).toBe(false);
  });

  it("refuses what is not an origin", () => {
    expect(originAllowed("null", ["https://a.example"])).toBe(false);
    expect(originAllowed("https://a.example/path", ["https://a.example"])).toBe(false);
    expect(originAllowed("not a url", ["https://a.example"])).toBe(false);
  });
});

describe("parseCommand", () => {
  it("takes a prefill and nothing else", () => {
    expect(parseCommand({ agentcanvas: 1, type: "prefill", text: "Hello" })).toEqual({ agentcanvas: 1, type: "prefill", text: "Hello" });
    expect(parseCommand({ agentcanvas: 1, type: "prefill", text: "x".repeat(MAX_PREFILL + 5) })!.text).toHaveLength(MAX_PREFILL);
    expect(parseCommand({ type: "prefill", text: "Hello" })).toBeNull(); // another script's message
    expect(parseCommand({ agentcanvas: 2, type: "prefill", text: "Hello" })).toBeNull();
    expect(parseCommand({ agentcanvas: 1, type: "prefill" })).toBeNull();
    expect(parseCommand({ agentcanvas: 1, type: "eval", text: "alert(1)" })).toBeNull();
    expect(parseCommand("prefill")).toBeNull();
    expect(parseCommand(null)).toBeNull();
  });
});

describe("postToParent", () => {
  function framed(referrer: string) {
    const post = vi.fn();
    vi.spyOn(window, "parent", "get").mockReturnValue({ postMessage: post } as unknown as Window);
    vi.spyOn(document, "referrer", "get").mockReturnValue(referrer);
    return post;
  }

  it("tells the framing site, addressed to its own origin", () => {
    const post = framed("https://www.example.com/shop?x=1");
    expect(postToParent({ type: "reply", text: "Hi" }, ["https://www.example.com"])).toBe(true);
    expect(post).toHaveBeenCalledWith({ agentcanvas: 1, type: "reply", text: "Hi" }, "https://www.example.com");
  });

  it("tells nothing to a site the link does not allow", () => {
    const post = framed("https://evil.example/");
    expect(postToParent({ type: "reply", text: "Hi" }, ["https://www.example.com"])).toBe(false);
    expect(post).not.toHaveBeenCalled();
  });

  it("tells nothing when the framing site is unknown and the link names sites", () => {
    const post = framed("");
    expect(postToParent({ type: "ready" }, ["https://www.example.com"])).toBe(false);
    expect(postToParent({ type: "ready" }, [])).toBe(true); // any site may frame: no secret to keep
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("does nothing outside a frame", () => {
    expect(postToParent({ type: "ready" }, [])).toBe(false); // jsdom: the window is its own parent
  });
});
