/** Loading, error (with retry) and empty states for a TanStack query (M6-T8): no screen stays blank. */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "../api/client";

interface QueryLike {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  data?: unknown;
  refetch: () => unknown;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.status >= 500 ? `the server failed (${error.status}): ${error.message}` : error.message;
  if (error instanceof TypeError) return "the API cannot be reached — is the server running?";
  return error instanceof Error ? error.message : String(error);
}

/** Renders the state of `q` when it is not "loaded with data", or nothing (then the caller renders the data). */
export function QueryStatus({ q, what, empty }: { q: QueryLike; what: string; empty?: ReactNode }) {
  if (q.isLoading) return <p className="muted skeleton" role="status">Loading {what}…</p>;
  if (q.isError) {
    return (
      <div className="error-box" role="alert">
        Could not load {what}: {errorText(q.error)}{" "}
        <button type="button" className="small" onClick={() => void q.refetch()}>Retry</button>
      </div>
    );
  }
  if (empty && Array.isArray(q.data) && q.data.length === 0) return <div className="empty-state">{empty}</div>;
  return null;
}

/** A page's code that could not be downloaded (a deploy replaced its chunk, or the network dropped), as each
 * browser words it. */
export function isChunkLoadError(error: unknown): boolean {
  return error instanceof Error && /dynamically imported module|Importing a module script failed|Unable to preload CSS|ChunkLoadError/i
    .test(`${error.name} ${error.message}`);
}

/** Catches render errors so one broken panel never blanks the whole app. */
export class ErrorBoundary extends Component<
  { children: ReactNode; label?: string; resetKey?: unknown }, { error: Error | null; resetKey?: unknown }
> {
  state = { error: null as Error | null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  /** A new `resetKey` (another page) shows the children again, without remounting them as a `key` would. */
  static getDerivedStateFromProps(props: { resetKey?: unknown }, state: { resetKey?: unknown }) {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ui.crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    if (isChunkLoadError(this.state.error)) {
      // not a crash, and "Try again" cannot help: the browser keeps the failed import
      return (
        <div className="error-box" role="alert">
          <b>{this.props.label ?? "This view"} could not be downloaded.</b>{" "}
          <span className="muted">AgentCanvas was probably updated since this tab was opened, or the connection dropped. Reload to get the current version.</span>{" "}
          <button type="button" className="small primary" onClick={() => location.reload()}>Reload</button>
        </div>
      );
    }
    return (
      <div className="error-box" role="alert">
        <b>{this.props.label ?? "This view"} crashed:</b> {this.state.error.message}{" "}
        {this.state.error instanceof TypeError && (
          <span className="muted">
            The data it received was not what it expects: this page and the server may be different versions. Reload to get the matching page.{" "}
          </span>
        )}
        <button type="button" className="small" onClick={() => this.setState({ error: null })}>Try again</button>{" "}
        <button type="button" className="small" onClick={() => location.reload()}>Reload</button>
      </div>
    );
  }
}
