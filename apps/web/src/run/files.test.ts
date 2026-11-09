import { describe, expect, it } from "vitest";
import { normalize } from "../ir/model";
import { fileFields, withFiles } from "./files";
import { sampleInput } from "./RunPanel";

const wf = normalize({
  id: "w", name: "looks",
  state: { channels: [
    { name: "question" },
    { name: "photo", type: { type: "object", format: "file", contentMediaType: "image/*" } },
    { name: "docs", type: { type: "array", items: { type: "object", format: "file" } } },
    { name: "out", io: "out" },
  ] },
});
const ref = (id: string) => ({ kind: "file" as const, id, name: `${id}.png`, media_type: "image/png", size: 1 });

describe("file fields in the Run tab", () => {
  it("finds the input channels that take files", () => {
    expect(fileFields(wf)).toEqual([
      { name: "photo", accept: "image/*", multiple: false },
      { name: "docs", accept: undefined, multiple: true },
    ]);
  });

  it("leaves them out of the sample JSON, which would show an empty object for a file", () => {
    expect(JSON.parse(sampleInput(wf))).toEqual({ question: "" });
  });

  it("puts the attached files into the run's input as references", () => {
    const fields = fileFields(wf);
    expect(withFiles({ question: "q" }, fields, { photo: [ref("a")], docs: [ref("b"), ref("c")] })).toEqual({
      question: "q", photo: ref("a"), docs: [ref("b"), ref("c")],
    });
    expect(withFiles({ question: "q", photo: "typed by hand" }, fields, {})).toEqual({ question: "q", docs: [] });
  });
});
