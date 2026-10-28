// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown, parseBlocks, slug } from "./Markdown";

afterEach(cleanup);

describe("parseBlocks", () => {
  it("splits headings, lists, quotes, code and paragraphs", () => {
    const src = "## Plan\n\n1. Read it\n2. Fix it\n   more of two\n\n- a\n- b\n\n> note\n\n```python\nx = 1\n```\nplain\ntext";
    expect(parseBlocks(src)).toEqual([
      { kind: "h", level: 2, text: "Plan" },
      { kind: "ol", items: ["Read it", "Fix it more of two"] },
      { kind: "ul", items: ["a", "b"] },
      { kind: "quote", text: "note" },
      { kind: "code", lang: "python", text: "x = 1" },
      { kind: "p", text: "plain\ntext" },
    ]);
  });
  it("shows an unclosed code block as code (a reply that is still streaming)", () => {
    expect(parseBlocks("```\npartial")).toEqual([{ kind: "code", lang: "", text: "partial" }]);
  });
});

describe("Markdown", () => {
  it("renders inline marks and never passes HTML through", () => {
    const { container } = render(<Markdown text={"**Bold** and *it* and `code` <img src=x onerror=alert(1)> [guide](/help) [bad](javascript:alert(1))"} />);
    expect(container.querySelector("strong")?.textContent).toBe("Bold");
    expect(container.querySelector("em")?.textContent).toBe("it");
    expect(container.querySelector("code")?.textContent).toBe("code");
    expect(container.querySelector("img")).toBeNull(); // shown as text
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    const links = [...container.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/help"]); // javascript: becomes plain text
    expect(container.textContent).toContain("bad");
  });
  it("gives code blocks a Copy button", () => {
    const { getByRole } = render(<Markdown text={"```json\n{}\n```"} />);
    expect(getByRole("button", { name: "Copy" })).toBeTruthy();
  });
});

describe("guide pages", () => {
  it("reads pipe tables, with escaped bars kept in a cell", () => {
    expect(parseBlocks("| Variable | Does |\n|---|:---:|\n| `A` | one \\| two |\n| B | three |")).toEqual([
      { kind: "table", head: ["Variable", "Does"], rows: [["`A`", "one | two"], ["B", "three"]] },
    ]);
  });
  it("gives headings anchors and resolves the guide's links", () => {
    const { container } = render(
      <Markdown page text={"# Deploy it\n\nSee [models](models.md#local-models) and ![a diagram](x.png)."}
        link={(href) => href.replace(/^([a-z-]+)\.md/, "/guide/$1")} />,
    );
    expect(container.querySelector("h1")?.id).toBe("deploy-it");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/guide/models#local-models");
    expect(container.querySelector("em")?.textContent).toBe("a diagram"); // images show their description
    expect(slug("Run it **locally** (Docker)!")).toBe("run-it-locally-docker");
    const wrapped = render(<Markdown page text={"one\ntwo  \nthree"} />).container;
    expect(wrapped.querySelectorAll("br")).toHaveLength(1); // wrapped source joins; two trailing spaces break
    expect(wrapped.textContent).toBe("one twothree");
  });
});
