/** Use a deployment from other agents (S15): its MCP server and A2A agent addresses, ready to paste. */
import type { DeploymentOut } from "../api/generated";

export function connectSnippets(base: string, depId: string, key: string) {
  const mcp = `${base}/v1/deployments/${depId}/mcp`;
  const a2a = `${base}/v1/deployments/${depId}/a2a`;
  const claudeDesktop = JSON.stringify(
    { mcpServers: { [`agentcanvas-${depId}`]: { command: "npx", args: ["-y", "mcp-remote", mcp, "--header", `Authorization: Bearer ${key}`] } } },
    null, 2,
  );
  return { mcp, a2a, card: `${a2a}/.well-known/agent-card.json`, claudeDesktop };
}

export function ConnectSection({ deployment, apiKey }: { deployment: DeploymentOut; apiKey?: string }) {
  const s = connectSnippets(`${location.origin}/api`, deployment.id, apiKey ?? "<your key>");
  return (
    <details className="connect small" data-testid={`connect-${deployment.environment}`}>
      <summary>Use from other agents (MCP, A2A)</summary>
      <p>
        This deployment is also an <b>MCP server</b> (Claude Desktop, Cursor, ChatGPT and agent frameworks call it as a
        tool) and an <b>A2A agent</b>. Both use the deployment's API key.
      </p>
      <label>MCP server (Streamable HTTP)</label>
      <code className="mono" data-testid="connect-mcp">{s.mcp}</code>
      <label>A2A agent card</label>
      <code className="mono">{s.card}</code>
      <label>Claude Desktop: add to claude_desktop_config.json</label>
      <pre className="json small" data-testid="connect-claude">{s.claudeDesktop}</pre>
      {!apiKey && <p className="muted">Replace &lt;your key&gt; with the key shown when you published or rotated it.</p>}
    </details>
  );
}
