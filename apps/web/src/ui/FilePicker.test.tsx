// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileRef } from "../ir/files";
import { FilePicker, formatSize, refuse } from "./FilePicker";

const png = (name = "me.png") => new File([new Uint8Array(2048)], name, { type: "image/png" });
const ref = (over: Partial<FileRef> = {}): FileRef => ({ kind: "file", id: "fil_1", name: "me.png", media_type: "image/png", size: 2048, ...over });

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);

function Harness({ upload, accept }: { upload: (f: File) => Promise<FileRef>; accept?: string }) {
  const [value, setValue] = useState<FileRef[]>([]);
  return <FilePicker label="photo" accept={accept} value={value} upload={upload} onChange={setValue} />;
}

describe("refuse", () => {
  it("says why a file cannot be attached before sending it", () => {
    expect(refuse(png())).toBeNull();
    expect(refuse({ name: "big.pdf", size: 11 * 1024 * 1024, type: "application/pdf" })).toMatch(/larger than 10 MB/);
    expect(refuse({ name: "a.txt", size: 0, type: "text/plain" })).toMatch(/empty/);
    expect(refuse({ name: "a.pdf", size: 5, type: "application/pdf" }, "image/*")).toMatch(/not an image/);
    expect(refuse(png(), "image/*")).toBeNull();
    expect(formatSize(2048)).toBe("2 KB");
  });
});

describe("FilePicker", () => {
  it("uploads a picked image and previews it", async () => {
    const upload = vi.fn(async () => ref());
    render(<Harness upload={upload} accept="image/*" />);
    fireEvent.change(screen.getByLabelText("photo file"), { target: { files: [png()] } });
    expect(await screen.findByAltText("Preview of me.png")).toHaveProperty("src", "blob:preview");
    expect(upload).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("remove me.png"));
    expect(screen.queryByAltText("Preview of me.png")).toBeNull();
  });

  it("takes a dropped file", async () => {
    const upload = vi.fn(async () => ref({ id: "fil_2", name: "scan.pdf", media_type: "application/pdf" }));
    render(<Harness upload={upload} />);
    fireEvent.drop(screen.getByTestId("file-photo"), { dataTransfer: { files: [new File(["%PDF-1.7"], "scan.pdf", { type: "application/pdf" })] } });
    expect(await screen.findByText("scan.pdf")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull(); // a PDF is a name, not a picture
  });

  it("shows no image for a file the server did not call an image", async () => {
    render(<Harness upload={async () => ref({ media_type: "text/plain" })} />);
    fireEvent.change(screen.getByLabelText("photo file"), { target: { files: [png()] } });
    await screen.findByText(/me.png/);
    expect(screen.queryByRole("img")).toBeNull();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("explains a refusal and does not send the file", async () => {
    const upload = vi.fn(async () => ref());
    render(<Harness upload={upload} accept="image/*" />);
    fireEvent.change(screen.getByLabelText("photo file"), { target: { files: [new File(["x"], "a.pdf", { type: "application/pdf" })] } });
    expect((await screen.findByRole("alert")).textContent).toMatch(/not an image/);
    expect(upload).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the upload fails", async () => {
    render(<Harness upload={async () => { throw new Error("'a.exe': this kind of file is not accepted"); }} />);
    fireEvent.change(screen.getByLabelText("photo file"), { target: { files: [png("a.exe")] } });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/not accepted/));
  });
});
