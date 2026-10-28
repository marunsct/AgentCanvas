import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router";
import { DialogHost } from "../ui/dialog";
import { GlobalErrors } from "../ui/GlobalErrors";
import { shouldRetry } from "../api/retry";
import { ErrorBoundary } from "../ui/QueryStatus";
import { AppShell } from "./AppShell";
import { AuthGate } from "./AuthGate";
import { HomePage } from "./HomePage";

// each page is its own chunk: the home page does not download the editor, CodeMirror or ELK
const EditorPage = lazy(() => import("../editor/EditorPage").then((m) => ({ default: m.EditorPage })));
const EventsPage = lazy(() => import("../events/EventsPage").then((m) => ({ default: m.EventsPage })));
const InboxPage = lazy(() => import("../inbox/InboxPage").then((m) => ({ default: m.InboxPage })));
const MemoryPage = lazy(() => import("../memory/MemoryPage").then((m) => ({ default: m.MemoryPage })));
const KnowledgePage = lazy(() => import("../knowledge/KnowledgePage").then((m) => ({ default: m.KnowledgePage })));
const EvaluationPage = lazy(() => import("../evaluation/EvaluationPage").then((m) => ({ default: m.EvaluationPage })));
const SettingsPage = lazy(() => import("../settings/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const SharePage = lazy(() => import("../share/SharePage").then((m) => ({ default: m.SharePage })));
const PlaygroundPage = lazy(() => import("../playground/PlaygroundPage").then((m) => ({ default: m.PlaygroundPage })));
const WorkspacePage = lazy(() => import("../workspace/WorkspacePage").then((m) => ({ default: m.WorkspacePage })));

// one retry: an unreachable API shows its error (with a Retry button) in seconds, not after three back-offs
const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: shouldRetry } } });

function NotFound() {
  return <div className="page"><h2>Page not found</h2><Link to="/">Go home</Link></div>;
}

export function App() {
  // S23: a share link's chat page (/s/<token>) is public: no sign-in, no project, none of the app around it
  if (location.pathname.startsWith("/s/")) {
    return (
      <>
        <GlobalErrors />
        <Suspense fallback={<p className="share-loading" role="status">Loading…</p>}><SharePage /></Suspense>
      </>
    );
  }
  return (
    <AuthGate>
    <GlobalErrors />
    <DialogHost />
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ErrorBoundary label="This page">
        <Suspense fallback={<div className="page muted" role="status">Loading…</div>}>
        <Routes>
          <Route path="/workflows/:id" element={<EditorPage />} />
          <Route element={<AppShell />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/workflows/:id/evaluation" element={<EvaluationPage />} />
            <Route path="/workflows/:id/workspace" element={<WorkspacePage />} />
            <Route path="/settings/:section?" element={<SettingsPage />} />
            <Route path="/playground" element={<PlaygroundPage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/workspace" element={<WorkspacePage />} />
            <Route path="/events" element={<EventsPage />} />
            {/* the addresses these had before Settings existed */}
            <Route path="/setup" element={<Navigate to="/settings/models" replace />} />
            <Route path="/members" element={<Navigate to="/settings/members" replace />} />
            <Route path="/audit" element={<Navigate to="/settings/audit" replace />} />
            <Route path="/policies" element={<Navigate to="/settings/policies" replace />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
        </Suspense>
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
    </AuthGate>
  );
}
