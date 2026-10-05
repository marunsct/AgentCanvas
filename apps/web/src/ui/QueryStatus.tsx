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

/** Catches render errors so one broken panel never blanks the whole app. */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ui.crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="error-box" role="alert">
        <b>{this.props.label ?? "This view"} crashed:</b> {this.state.error.message}{" "}
        <button type="button" className="small" onClick={() => this.setState({ error: null })}>Try again</button>{" "}
        <button type="button" className="small" onClick={() => location.reload()}>Reload</button>
      </div>
    );
  }
}
