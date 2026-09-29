import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import { EditorPage } from "../editor/EditorPage";
import { InboxPage } from "../inbox/InboxPage";
import { MemoryPage } from "../memory/MemoryPage";
import { WorkspacePage } from "../workspace/WorkspacePage";
import { HomePage } from "./HomePage";

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

function NotFound() {
  return <div className="page"><h2>Page not found</h2><Link to="/">Go home</Link></div>;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/workflows/:id" element={<EditorPage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
