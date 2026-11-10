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
  const id = `connect-${deployment.id}`;
  return (
    <details className="connect small" data-testid={`connect-${deployment.environment}`}>
      <summary>Use from other agents (MCP, A2A)</summary>
      <p>
        This deployment is also an <b>MCP server</b> (Claude Desktop, Cursor, ChatGPT and agent frameworks call it as a
        tool) and an <b>A2A agent</b>. Both use the deployment's API key.
        {(deployment as DeploymentOut & { oauth?: boolean }).oauth && (
          <> People can also <b>sign in with OAuth</b>: clients that support it need only the address below.</>
        )}
      </p>
      <p id={`${id}-mcp`} className="connect-label">MCP server (Streamable HTTP)</p>
      <code className="mono" aria-labelledby={`${id}-mcp`} data-testid="connect-mcp">{s.mcp}</code>
      <p id={`${id}-a2a`} className="connect-label">A2A agent card</p>
      <code className="mono" aria-labelledby={`${id}-a2a`}>{s.card}</code>
      <p id={`${id}-claude`} className="connect-label">Claude Desktop: add to claude_desktop_config.json</p>
      <pre className="json small" aria-labelledby={`${id}-claude`} data-testid="connect-claude">{s.claudeDesktop}</pre>
      {!apiKey && <p className="muted">Replace &lt;your key&gt; with the key shown when you published or rotated it.</p>}
    </details>
  );
}
