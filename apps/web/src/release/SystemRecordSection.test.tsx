// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { DeploymentOut } from "../api/generated";
import type { RecordFields, SystemRecordOut } from "./SystemRecordSection";

const puts: RecordFields[] = [];
const downloads: string[] = [];
let builder = true;
let record: SystemRecordOut;

function made(fields: Partial<SystemRecordOut["record"]> = {}, notes: string[] = []): SystemRecordOut {
  return {
    record: {
      purpose: "", owner_id: null, risk_class: null, risk_rationale: "", data_categories: [], end_user_facing: null,
      review_date: null, owner_email: null, owner_member: true, updated_by_email: null, updated_at: null, ...fields,
    },
    version: { semver: "1.2.0" }, models: [{ model: "anthropic:claude-sonnet-5-5" }], subprocessors: [{ name: "Anthropic" }],
    tools: [], approvals: [], ungated: ["send_email"],
    evaluation: { release_gate: null, experiment: null, red_team: null, online: [], test_cases: 0 }, history: [], notes,
  };
}

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string, init?: { method?: string; json?: RecordFields }) => {
    if (path === "/org/members") return [
      { user_id: "usr_ada", email: "ada@example.com", display_name: "Ada", role: "admin", invited: false },
      { user_id: "usr_gone", email: "gone@example.com", display_name: null, role: "viewer", invited: false, deactivated: true },
    ];
    if (init?.method === "PUT" && init.json) {
      puts.push(init.json);
      record = made(init.json, init.json.risk_class === "high" ? ["Marked high risk, but no step asks a person: …"] : []);
      return record;
    }
    return record;
  }),
  apiDownload: vi.fn(async (path: string) => { downloads.push(path); }),
}));
vi.mock("../api/session", () => ({ can: () => builder }));

const { SystemRecordSection } = await import("./SystemRecordSection");
const deployment = { id: "d1", environment: "prod" } as DeploymentOut;

beforeEach(() => {
  puts.length = 0;
  downloads.length = 0;
  builder = true;
  record = made({}, ["No purpose, owner or risk class is recorded yet."]);
});
afterEach(cleanup);

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><SystemRecordSection deployment={deployment} /></QueryClientProvider>);
}

it("shows the notes and the evidence the server read", async () => {
  show();
  expect((await screen.findByTestId("record-notes")).textContent).toContain("No purpose, owner or risk class");
  expect(screen.getByTestId("record-evidence").textContent).toBe(
    "v1.2.0: 1 model, 1 outside service, 0 tools, 0 approval steps (1 acting without one); evaluation evidence: none yet.",
  );
});

it("saves what a builder fills in, and asks why before a risk class is saved", async () => {
  show();
  fireEvent.change(await screen.findByLabelText("Purpose"), { target: { value: "  Screens job applications " } });
  fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "usr_ada" } });
  expect(screen.queryByRole("option", { name: "gone@example.com" })).toBeNull(); // a deactivated member is not offered
  fireEvent.change(screen.getByLabelText("Risk class"), { target: { value: "high" } });
  const save = screen.getByRole("button", { name: "Save record" });
  expect(save).toHaveProperty("disabled", true);
  expect(screen.getByText("Say why it has this risk class.")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Why this risk class"), { target: { value: "decides about people's work" } });
  fireEvent.click(screen.getByLabelText("personal data"));
  fireEvent.change(screen.getByLabelText("Other (separate with commas)"), { target: { value: "CVs, personal data" } });
  fireEvent.change(screen.getByLabelText("Its end users"), { target: { value: "false" } });
  fireEvent.change(screen.getByLabelText("Next review"), { target: { value: "2027-05-01" } });
  await act(async () => { fireEvent.click(save); });
  expect(puts).toEqual([{
    purpose: "Screens job applications", owner_id: "usr_ada", risk_class: "high", risk_rationale: "decides about people's work",
    data_categories: ["personal data", "CVs"], end_user_facing: false, review_date: "2027-05-01",
  }]);
  await waitFor(() => expect(screen.getByTestId("record-notes").textContent).toContain("Marked high risk"));
  expect(screen.getByRole("status").textContent).toBe("Saved.");
  expect(screen.getByLabelText("Other (separate with commas)")).toHaveProperty("value", "CVs");
});

it("lets anyone download the record and only builders change it", async () => {
  builder = false;
  record = made({ purpose: "Greets visitors", owner_id: "usr_left", owner_email: null, owner_member: false });
  show();
  expect((await screen.findByLabelText("Purpose")).matches(":disabled")).toBe(true); // the whole form is read-only
  expect(screen.queryByRole("button", { name: "Save record" })).toBeNull();
  expect(screen.getByRole("option", { name: "usr_left (no longer a member)" })).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Download (Markdown)" })); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Download (HTML, prints as PDF)" })); });
  expect(downloads).toEqual(["/deployments/d1/system-record/export?format=markdown", "/deployments/d1/system-record/export?format=html"]);
});
