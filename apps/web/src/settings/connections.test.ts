import { describe, expect, it } from "vitest";
import type { ConnectionOut } from "../api/generated";
import { connectionFor, resourceOf } from "./connections";

const row = (resource: string, owner: string): ConnectionOut =>
  ({ id: `oac_${owner}`, resource, host: "mcp.test", owner, status: "active", scope: null, expires_at: null, created_at: "", updated_at: "" }) as unknown as ConnectionOut;

describe("connections", () => {
  it("writes an address the way the API keys it", () => {
    expect(resourceOf("HTTPS://MCP.Test:443/mcp#frag")).toBe("https://mcp.test/mcp");
    expect(resourceOf("https://mcp.test")).toBe("https://mcp.test/");
    expect(resourceOf("nope")).toBeNull();
    expect(resourceOf(undefined)).toBeNull();
  });

  it("finds the caller's own connection or the service's, by what the connector acts as", () => {
    const list = [row("https://mcp.test/mcp", "service"), row("https://mcp.test/mcp", "you"), row("https://other.test/", "you")];
    expect(connectionFor(list, "https://mcp.test/mcp", "starter")?.owner).toBe("you");
    expect(connectionFor(list, "https://mcp.test/mcp", "service")?.owner).toBe("service");
    expect(connectionFor(list, "https://nowhere.test/", "starter")).toBeUndefined();
    expect(connectionFor(undefined, "https://mcp.test/mcp", "starter")).toBeUndefined();
  });
});
