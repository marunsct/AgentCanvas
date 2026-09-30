import { describe, expect, it } from "vitest";
import { normalize } from "../ir/model";
import { triage } from "../test/fixtures";
import { isChat } from "./RunPanel";

describe("isChat", () => {
  it("uses chat mode only when messages is the sole input", () => {
    expect(isChat(triage())).toBe(false);
    const chat = normalize({ id: "w", name: "c", state: { channels: [{ name: "messages", reducer: "add_messages" }] } });
    expect(isChat(chat)).toBe(true);
  });
});
