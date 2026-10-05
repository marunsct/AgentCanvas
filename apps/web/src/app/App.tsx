import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import { ErrorBoundary } from "../ui/QueryStatus";
import { AuthGate } from "./AuthGate";
import { HomePage } from "./HomePage";

// each page is its own chunk: the home page does not download the editor, CodeMirror or ELK
const EditorPage = lazy(() => import("../editor/EditorPage").then((m) => ({ default: m.EditorPage })));
const EventsPage = lazy(() => import("../events/EventsPage").then((m) => ({ default: m.EventsPage })));
const InboxPage = lazy(() => import("../inbox/InboxPage").then((m) => ({ default: m.InboxPage })));
const MemoryPage = lazy(() => import("../memory/MemoryPage").then((m) => ({ default: m.MemoryPage })));
const AuditPage = lazy(() => import("./AuditPage").then((m) => ({ default: m.AuditPage })));
const MembersPage = lazy(() => import("./MembersPage").then((m) => ({ default: m.MembersPage })));
const WorkspacePage = lazy(() => import("../workspace/WorkspacePage").then((m) => ({ default: m.WorkspacePage })));

// one retry: an unreachable API shows its error (with a Retry button) in seconds, not after three back-offs
const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });

function NotFound() {
  return <div className="page"><h2>Page not found</h2><Link to="/">Go home</Link></div>;
}

export function App() {
  return (
    <AuthGate>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ErrorBoundary label="This page">
        <Suspense fallback={<div className="page muted" role="status">Loading…</div>}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/workflows/:id" element={<EditorPage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/members" element={<MembersPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
    </AuthGate>
  );
}
