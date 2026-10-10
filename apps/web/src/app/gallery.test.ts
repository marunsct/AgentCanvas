import { describe, expect, it } from "vitest";
import { categories, filterTemplates, type GalleryTemplate } from "./gallery";

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
