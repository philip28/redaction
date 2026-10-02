import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

/**
 * Stops one broken component from taking the page with it.
 *
 * React unmounts the entire tree when a render throws, so a single bad field turns into
 * a blank white screen with nothing on it to explain why — the worst possible failure
 * for a tool people run against confidential documents, because a blank page and a
 * finished job look identical. This catches the throw, keeps the rest of the interface
 * alive, and shows what happened.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; fallbackTitle: string; retryLabel: string },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ui] render failed:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="notice">
        <strong>{this.props.fallbackTitle}</strong>
        <div className="mono" style={{ marginTop: 6, fontSize: 12 }}>
          {this.state.error.message}
        </div>
        <button
          type="button"
          className="btn-quiet"
          style={{ marginTop: 10 }}
          onClick={() => this.setState({ error: null })}
        >
          {this.props.retryLabel}
        </button>
      </div>
    );
  }
}
