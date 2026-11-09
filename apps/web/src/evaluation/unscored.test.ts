import { describe, expect, it } from "vitest";
import { unscoredNote } from "./EvaluationPage";

describe("experiment summary", () => {
  it("says how many completed examples an evaluator left unscored", () => {
    expect(unscoredNote({ unscored: 7 }, 10)).toBe(" · 7 of 10 unscored");
    expect(unscoredNote({ unscored: 0 }, 10)).toBeNull();
    expect(unscoredNote({}, 10)).toBeNull(); // an experiment summarised before the field existed
  });
});
