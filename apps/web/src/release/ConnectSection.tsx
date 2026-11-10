/** Use a deployment from other agents (S15) and agent front ends (F-8): its MCP server, A2A agent and AG-UI
 * addresses, ready to paste. */
import type { DeploymentOut } from "../api/generated";

export function connectSnippets(base: string, depId: string, key: string) {
  const mcp = `${base}/v1/deployments/${depId}/mcp`;
  const a2a = `${base}/v1/deployments/${depId}/a2a`;
  const agui = `${base}/v1/deployments/${depId}/ag-ui`;
  const claudeDesktop = JSON.stringify(
    { mcpServers: { [`agentcanvas-${depId}`]: { command: "npx", args: ["-y", "mcp-remote", mcp, "--header", `Authorization: Bearer ${key}`] } } },
    null, 2,
  );
  return { mcp, a2a, agui, card: `${a2a}/.well-known/agent-card.json`, claudeDesktop };
}

export function ConnectSection({ deployment, apiKey }: { deployment: DeploymentOut; apiKey?: string }) {
  const s = connectSnippets(`${location.origin}/api`, deployment.id, apiKey ?? "<your key>");
  const id = `connect-${deployment.id}`;
  return (
    <details className="connect small" data-testid={`connect-${deployment.environment}`}>
      <summary>Use from other agents and front ends (MCP, A2A, AG-UI)</summary>
      <p>
        This deployment is also an <b>MCP server</b> (Claude Desktop, Cursor, ChatGPT and agent frameworks call it as a
        tool), an <b>A2A agent</b> and an <b>AG-UI agent</b> (CopilotKit and other agent front ends). All use the
        deployment's API key: keep it on your server, never in a web page.
        {(deployment as DeploymentOut & { oauth?: boolean }).oauth && (
          <> People can also <b>sign in with OAuth</b> to the MCP and A2A agents: clients that support it need only
          the address below.</>
        )}
      </p>
      <p id={`${id}-mcp`} className="connect-label">MCP server (Streamable HTTP)</p>
      <code className="mono" aria-labelledby={`${id}-mcp`} data-testid="connect-mcp">{s.mcp}</code>
      <p id={`${id}-a2a`} className="connect-label">A2A agent card</p>
      <code className="mono" aria-labelledby={`${id}-a2a`}>{s.card}</code>
      <p id={`${id}-agui`} className="connect-label">AG-UI endpoint (an HttpAgent in CopilotKit's runtime)</p>
      <code className="mono" aria-labelledby={`${id}-agui`} data-testid="connect-agui">{s.agui}</code>
      <p id={`${id}-claude`} className="connect-label">Claude Desktop: add to claude_desktop_config.json</p>
      <pre className="json small" aria-labelledby={`${id}-claude`} data-testid="connect-claude">{s.claudeDesktop}</pre>
      {!apiKey && <p className="muted">Replace &lt;your key&gt; with the key shown when you published or rotated it.</p>}
    </details>
  );
}
