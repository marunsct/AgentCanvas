// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ModelSelect } from "./ModelSelect";

afterEach(cleanup);
const OPTIONS = [{ id: "lmstudio:openai/gpt-oss-20b", label: "openai/gpt-oss-20b", provider: "LM Studio (local)", local: true }];

describe("ModelSelect", () => {
  it("offers the models set up here, and keeps a saved one that is not, marked", () => {
    render(<ModelSelect label="judge" options={OPTIONS} value="openai:gpt-5.5" empty="the project's model" onChange={() => {}} />);
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(["the project's model", "openai/gpt-oss-20b · LM Studio (local)", "gpt-5.5 (not set up now)"]);
    expect(screen.getByLabelText("judge")).toHaveProperty("value", "openai:gpt-5.5");
  });
});
