// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { NEW_RED_TEAM, RedTeamFields, redTeamBody, type RedTeamDraft } from "./RedTeam";

afterEach(cleanup);

describe("red team form", () => {
  it("builds the request from the form, keeping the count in the API's range", () => {
    const rt = { ...NEW_RED_TEAM, per_category: "40", attacker: " openai:gpt-5 ", policy: " travel only ", categories: ["jailbreak"] };
    expect(redTeamBody(rt, [{ version: null }])).toEqual({
      categories: ["jailbreak"], per_category: 10, attacker_model: "openai:gpt-5", judge_model: null,
      policy: "travel only", targets: [{ version: null }],
    });
    expect(redTeamBody({ ...NEW_RED_TEAM, per_category: "x" }, []).per_category).toBe(4);
  });

  it("lets the person pick categories", () => {
    let latest: RedTeamDraft = NEW_RED_TEAM;
    function Host() {
      const [v, setV] = useState(NEW_RED_TEAM);
      latest = v;
      return <RedTeamFields value={v} onChange={setV} />;
    }
    render(<Host />);
    fireEvent.click(screen.getByLabelText("Jailbreak"));
    expect(latest.categories).toEqual(["prompt_injection", "pii_extraction", "off_topic"]);
    fireEvent.click(screen.getByLabelText("Jailbreak"));
    expect(latest.categories).toContain("jailbreak");
  });

  it("offers multi-turn attacks for chat workflows, but not by default", () => {
    expect(NEW_RED_TEAM.categories).not.toContain("multi_turn");
    let latest: RedTeamDraft = NEW_RED_TEAM;
    function Host() {
      const [v, setV] = useState(NEW_RED_TEAM);
      latest = v;
      return <RedTeamFields value={v} onChange={setV} />;
    }
    render(<Host />);
    fireEvent.click(screen.getByLabelText("Multi-turn (chat workflows)"));
    expect(latest.categories).toContain("multi_turn");
    expect(redTeamBody(latest, []).categories).toContain("multi_turn");
  });
});
