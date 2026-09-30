import { Text } from "@codemirror/state";
import { describe, expect, it } from "vitest";
import { toDiagnostics } from "./CodeEditor";

describe("ruff diagnostics -> editor markers (M5-T2)", () => {
  it("maps 1-based line/column to offsets and clamps out-of-range positions", () => {
    const doc = Text.of(["import os", "def run(state):", "    return {}"]);
    const [unused, clamped] = toDiagnostics(doc, [
      { line: 1, column: 8, end_line: 1, end_column: 10, code: "F401", message: "`os` imported but unused" },
      { line: 9, column: 99, end_line: 9, end_column: 120, code: "W292", message: "no newline" },
    ]);
    expect(doc.sliceString(unused.from, unused.to)).toBe("os");
    expect(unused.severity).toBe("error");
    expect(clamped.from).toBe(doc.length);
    expect(clamped.severity).toBe("warning");
  });
});
