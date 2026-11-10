// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { DeploymentOut } from "../api/generated";
import { ConnectSection, connectSnippets } from "./ConnectSection";

describe("ConnectSection (S15, F-8)", () => {
  it("gives the MCP, A2A and AG-UI addresses of the deployment", () => {
    const s = connectSnippets("https://ac.example.com/api", "dep_1", "ac_key");
    expect(s.mcp).toBe("https://ac.example.com/api/v1/deployments/dep_1/mcp");
    expect(s.card).toBe("https://ac.example.com/api/v1/deployments/dep_1/a2a/.well-known/agent-card.json");
    expect(s.agui).toBe("https://ac.example.com/api/v1/deployments/dep_1/ag-ui");
  });

  it("shows the AG-UI endpoint and says to keep the key on the server", () => {
    render(<ConnectSection deployment={{ id: "dep_2", environment: "prod" } as unknown as DeploymentOut} />);
    expect(screen.getByTestId("connect-agui").textContent).toMatch(/\/v1\/deployments\/dep_2\/ag-ui$/);
    expect(screen.getByText(/keep it on your server/)).toBeTruthy();
  });
});
