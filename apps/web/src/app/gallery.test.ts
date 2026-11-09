import { describe, expect, it } from "vitest";
import { categories, filterTemplates, type GalleryTemplate, kitNote } from "./gallery";

const T: GalleryTemplate[] = [
  { id: "faq_bot", name: "Customer FAQ bot", description: "Answers from the FAQ", category: "Customer support", tags: ["chatbot"], difficulty: "beginner" },
  { id: "sql_helper", name: "SQL query helper", description: "Drafts a query", category: "Engineering", tags: ["sql", "analytics"], difficulty: "beginner" },
  { id: "code_review", name: "Code review assistant", description: "Reviews a diff", category: "Engineering", tags: ["code review"], difficulty: "intermediate" },
  { id: "chat_assistant", name: "Chat assistant", description: "Start here", category: "Getting started", tags: [] },
  { id: "support_triage", name: "Support triage (dev)", description: "A demo" },
];

describe("template gallery", () => {
  it("lists categories in the gallery order with counts, uncategorised last", () => {
    expect(categories(T)).toEqual([
      { name: "Getting started", count: 1 },
      { name: "Customer support", count: 1 },
      { name: "Engineering", count: 2 },
      { name: "Other", count: 1 },
    ]);
  });

  it("filters by level", () => {
    expect(filterTemplates(T, "", null, "intermediate").map((t) => t.id)).toEqual(["code_review"]);
    expect(filterTemplates(T, "", "Engineering", "beginner").map((t) => t.id)).toEqual(["sql_helper"]);
    expect(filterTemplates(T, "", null, "advanced")).toEqual([]);
  });

  it("filters by category", () => {
    expect(filterTemplates(T, "", "Engineering").map((t) => t.id)).toEqual(["sql_helper", "code_review"]);
    expect(filterTemplates(T, "", "Other").map((t) => t.id)).toEqual(["support_triage"]);
    expect(filterTemplates(T, "", null)).toHaveLength(5);
  });

  it("searches name, description, tags and difficulty; every word must match", () => {
    expect(filterTemplates(T, "analytics", null).map((t) => t.id)).toEqual(["sql_helper"]);
    expect(filterTemplates(T, "  REVIEW diff ", null).map((t) => t.id)).toEqual(["code_review"]);
    expect(filterTemplates(T, "intermediate", null).map((t) => t.id)).toEqual(["code_review"]);
    expect(filterTemplates(T, "support", null).map((t) => t.id)).toEqual(["faq_bot", "support_triage"]);
    expect(filterTemplates(T, "faq", "Engineering")).toEqual([]);
  });
});

describe("kitNote", () => {
  it("says what a template added beside the workflow, and which files it kept", () => {
    expect(kitNote({ files_written: ["events/a.py", "scripts/b.py"], files_kept: [], dataset_id: "ds_1", evaluators: 1, tests: 2, triggers: 1 }))
      .toBe("Also added 2 workspace files, a dataset, 1 evaluator, 2 saved tests, a schedule (switched off).");
    expect(kitNote({ files_written: [], files_kept: ["nodes/x.py"], evaluators: 0, tests: 0, triggers: 0 })).toBe("Kept your existing nodes/x.py.");
    expect(kitNote(null)).toBeNull();
    expect(kitNote({ files_written: [], files_kept: [], evaluators: 0, tests: 0, triggers: 0 })).toBeNull();
  });
});
