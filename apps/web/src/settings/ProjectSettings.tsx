/** Settings › Project › General: the project's name, and deleting it with everything in it (admins). */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import { PROJECT_ID, PROJECT_NAME, switchProject } from "../api/queries";
import { useCan } from "../api/session";
import { confirmDialog, promptDialog, toast } from "../ui/dialog";

export function ProjectSettings() {
  const admin = useCan("admin");
  const [name, setName] = useState(PROJECT_NAME);
  const projects = useQuery({ queryKey: ["projects", "all"], queryFn: () => apiFetch<{ id: string; name: string }[]>("/projects") });
  const rename = useMutation({
    mutationFn: () => apiFetch<{ name: string }>(`/projects/${PROJECT_ID}`, { method: "PATCH", json: { name: name.trim() } }),
    // the name is read once at start-up (top bar, Settings rail): start over so every place shows the new one
    onSuccess: () => { toast("Renamed"); setTimeout(() => location.reload(), 600); },
  });
  const others = (projects.data ?? []).filter((p) => p.id !== PROJECT_ID);
  const remove = useMutation({
    mutationFn: () => apiFetch(`/projects/${PROJECT_ID}`, { method: "DELETE" }),
    onSuccess: () => switchProject(others[0].id),
  });
  const askDelete = async () => {
    const typed = await promptDialog({
      title: `Delete ${PROJECT_NAME}?`,
      body: <>Its workflows, versions, published endpoints, runs, datasets, knowledge, secrets and workspace code are
        deleted. This cannot be undone. Type the project's name to confirm.</>,
      confirm: "Delete the project", danger: true, input: { label: "Project name", required: true },
    });
    if (typed === null) return;
    if (typed.trim() !== PROJECT_NAME) {
      await confirmDialog({ title: "Not deleted", body: "The name you typed does not match the project's name.", confirm: "OK", cancel: "Close" });
      return;
    }
    remove.mutate();
  };
  return (
    <>
      <section className="settings-card" aria-label="Project">
        <h2>General</h2>
        <p className="muted small">A project holds its own workflows, knowledge, secrets, settings and code.</p>
        <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); if (name.trim() && name.trim() !== PROJECT_NAME) rename.mutate(); }}>
          <input aria-label="project name" value={name} maxLength={100} disabled={!admin} onChange={(e) => setName(e.target.value)} />
          {admin && <button type="submit" className="primary" disabled={!name.trim() || name.trim() === PROJECT_NAME || rename.isPending}>Rename</button>}
        </form>
        {!admin && <p className="muted small">Only an admin can rename or delete a project.</p>}
        {rename.error && <p className="error-text small" role="alert">Could not rename the project: {(rename.error as Error).message}</p>}
        <p className="muted small">Project id <code>{PROJECT_ID}</code> (used in API calls and exports).</p>
      </section>
      {admin && (
        <section className="settings-card danger-zone" aria-label="Delete the project">
          <h2>Delete the project</h2>
          {others.length === 0
            ? <p className="muted small">This is the organisation's only project. Create another one (in the project menu at the top left) before deleting it.</p>
            : <p className="muted small">Deletes everything in it. You will move to {others[0].name}.</p>}
          <button type="button" className="danger-solid" disabled={others.length === 0 || remove.isPending} onClick={() => void askDelete()}
            data-testid="delete-project">Delete {PROJECT_NAME}…</button>
          {remove.error && <p className="error-text small" role="alert">Could not delete the project: {(remove.error as Error).message}</p>}
        </section>
      )}
    </>
  );
}
