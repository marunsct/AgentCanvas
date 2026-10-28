import { describe, expect, it } from "vitest";
import { buildTree } from "./FileTree";

describe("buildTree", () => {
  it("groups paths into folders, with a prefix stripped for display", () => {
    const f = (path: string) => ({ path, size: 1, modified: "" }) as never;
    const root = buildTree([f("README.md"), f("scripts/a.py"), f("scripts/lib/b.py"), f("flows/wf_1/tools/t.py")], "");
    expect(root.files.map((x) => x.name)).toEqual(["README.md"]);
    expect([...root.folders.keys()]).toEqual(["scripts", "flows"]);
    expect(root.folders.get("scripts")!.folders.get("lib")!.files[0]).toEqual({ name: "b.py", path: "scripts/lib/b.py", shown: "scripts/lib/b.py" });
    const own = buildTree([f("flows/wf_1/tools/t.py")], "flows/wf_1/");
    expect(own.folders.get("tools")!.files[0].shown).toBe("tools/t.py");
  });
});
