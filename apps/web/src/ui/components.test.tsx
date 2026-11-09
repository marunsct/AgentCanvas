// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Badge } from "./Badge";
import { Field } from "./Form";
import { Table } from "./Table";

afterEach(cleanup);

describe("Badge", () => {
  it("keeps the classes the stylesheet already colours", () => {
    render(<><Badge tone="ok">done</Badge><Badge tone="running">running</Badge><Badge>plain</Badge><Badge tone="beta" title="not final">beta</Badge></>);
    expect(screen.getByText("done").className).toBe("badge ok");
    expect(screen.getByText("running").className).toBe("badge status-running");
    expect(screen.getByText("plain").className).toBe("badge");
    expect(screen.getByText("beta").getAttribute("title")).toBe("not final");
  });
});

describe("Table", () => {
  it("always has column headers; a hidden one is still read out", () => {
    render(
      <Table columns={[{ label: "Name" }, { label: "Actions", hidden: true }]} className="wf-table" bodyTestId="rows">
        <tr><td>One</td><td /></tr>
      </Table>,
    );
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Actions"]);
    expect(screen.getByText("Actions").className).toBe("visually-hidden");
    expect(screen.getByRole("table").className).toBe("table wf-table");
    expect(screen.getByTestId("rows").textContent).toBe("One");
  });
});

describe("Field", () => {
  it("labels its control, shows the hint, and announces an error", () => {
    const { rerender } = render(<Field label="Size" hint="100 to 8000"><input type="number" /></Field>);
    expect(screen.getByLabelText("Size").tagName).toBe("INPUT");
    expect(screen.getByText("100 to 8000")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<Field label="Size" error="Too small"><input type="number" /></Field>);
    expect(screen.getByRole("alert").textContent).toBe("Too small");
    expect(screen.getByLabelText("Size")).toBeTruthy(); // the error is not part of the label's name
  });
});
