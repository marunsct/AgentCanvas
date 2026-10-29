// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { accepts, fileSchema, isFileRef, isFileSchema, isImage, schemaFor, typeLabel } from "./files";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));

const { useEditor } = await import("../editor/store");
const { StateDesigner } = await import("../inspector/StateDesigner");
afterEach(cleanup);

describe("file fields (G-5)", () => {
  it("are JSON Schema with format file", () => {
    expect(fileSchema()).toEqual({ type: "object", format: "file" });
    expect(isFileSchema(fileSchema("image/*"))).toBe(true);
    expect(isFileSchema({ type: "array", items: fileSchema() })).toBe(true);
    expect(isFileSchema({ type: "object" })).toBe(false);
    expect(accepts({ type: "array", items: fileSchema("image/*") })).toBe("image/*");
    expect(typeLabel(fileSchema("image/*"))).toBe("image");
    expect(typeLabel(fileSchema())).toBe("file");
    expect(typeLabel({ type: "number" })).toBe("number");
    expect(typeLabel(undefined)).toBe("string");
    expect(schemaFor("image")).toEqual(fileSchema("image/*"));
    expect(schemaFor("integer")).toEqual({ type: "integer" });
    expect(isFileRef({ kind: "file", id: "fil_1" })).toBe(true);
    expect(isFileRef("fil_1")).toBe(false);
    expect(isImage("image/png")).toBe(true);
    expect(isImage("image/svg+xml")).toBe(false); // an SVG is a document that runs scripts, never shown as an image
  });

  it("can be picked in the state designer", () => {
    const dispatch = vi.fn();
    useEditor.setState({ wf: triage(), dispatch, selected: null, diagnostics: [] });
    render(<StateDesigner />);
    const first = useEditor.getState().wf!.state.channels[0].name;
    fireEvent.change(screen.getByLabelText(`${first} type`), { target: { value: "image" } });
    const [[commands]] = dispatch.mock.calls as [[{ op: string; channels: { name: string; type: unknown }[] }[]]];
    expect(commands[0].channels[0].type).toEqual({ type: "object", format: "file", contentMediaType: "image/*" });
  });
});
