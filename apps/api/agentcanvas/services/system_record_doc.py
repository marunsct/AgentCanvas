"""A deployment's AI-system record as a document (Markdown or HTML) to hand an auditor or file in an AI inventory.

Both formats come from one list of sections, so they always say the same. Everything people or connected servers
wrote (names, purposes, tool names, addresses) is escaped: in Markdown no link, image, HTML or heading can come from
it, and the HTML page is plain text in markup the server wrote, served with a policy that allows no scripts.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from agentcanvas.services.system_record import RISK_CLASSES, ExperimentEvidence, SystemRecordOut

Format = Literal["markdown", "html"]
FINGERPRINT = 12  # characters of a pinned tool's fingerprint shown (the JSON record has all of it)


@dataclass
class Block:
    kind: Literal["fields", "table", "list", "text"]
    rows: list[list[str]] = field(default_factory=list)  # fields: [label, value]; table: cells
    headers: list[str] = field(default_factory=list)
    title: str = ""  # a sub-heading above the block


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)


def _when(value: datetime | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return value.isoformat()


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.0f}%"


def _num(value: Any) -> str:
    return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def _experiment(title: str, e: ExperimentEvidence | None, missing: str) -> list[Block]:
    if e is None:
        return [Block("text", [[missing]], title=title)]
    out = [Block("fields", title=title, rows=[
        ["Name", e.name], ["Dataset", f"{e.dataset} (version {e.dataset_version})"], ["Finished", _when(e.ended_at)],
    ])]  # fmt: skip
    out.append(Block("table", headers=["Target", "Version", "This version", "Pass rate", "Mean scores"], rows=[
        [t.label, t.version or "", "yes" if t.this_version else "no", _pct(t.pass_rate),
         ", ".join(f"{k} {_num(v)}" for k, v in t.evaluators.items())]
        for t in e.targets
    ]))  # fmt: skip
    return out


def sections(rec: SystemRecordOut) -> list[Section]:
    r = rec.record
    audience = {True: "the public (customers, visitors)", False: "only your own staff", None: "not stated"}
    owner = r.owner_email or (f"{r.owner_id} (no longer a member)" if r.owner_id else "")
    risk = RISK_CLASSES[r.risk_class].split(":")[0] if r.risk_class else ""
    summary = Section("Summary", [Block("fields", rows=[
        ["Purpose", r.purpose], ["Owner", owner],
        ["Risk class", risk + (f": {r.risk_rationale}" if r.risk_rationale else "")],
        ["Data categories", ", ".join(r.data_categories)], ["End users", audience[r.end_user_facing]],
        ["Review date", _when(r.review_date)],
        ["Status", ("published" if rec.active else "unpublished") + f", deployment {rec.deployment_id}"],
        ["Deployed since", _when(rec.published_at)],
        ["Record last changed", " ".join(x for x in (_when(r.updated_at), f"by {r.updated_by_email}"
                                                     if r.updated_by_email else "") if x)],
    ])])  # fmt: skip
    out = [summary]
    if rec.notes:
        out.append(Section("Notes", [Block("list", [[n] for n in rec.notes])]))

    v, c = rec.version, rec.changes
    version = Section("Version", [Block("fields", rows=[
        ["Version", f"v{v.semver}"], ["Message", v.message], ["Made by", v.created_by or ""],
        ["Made at", _when(v.created_at)], ["Workspace commit", v.workspace_commit or ""],
    ])])  # fmt: skip
    changed = [["Since", f"v{c.previous}" if c.previous else "(first version)"], ["Summary", c.summary]]
    changed += [[label, ", ".join(items)] for label, items in
                (("Added", c.added), ("Removed", c.removed), ("Changed", c.changed)) if items]  # fmt: skip
    if c.copilot:
        changed.append(["Copilot proposals accepted", "; ".join(c.copilot)])
    version.blocks.append(Block("fields", changed, title="Changes"))
    out.append(version)

    out.append(Section("Models", [
        Block("table", headers=["Block", "Model", "Provider", "Role", "Address", "Used by"],
              rows=[[m.block, m.model, m.provider_label, m.role, m.address or "", ", ".join(m.used_by)]
                    for m in rec.models])
        if rec.models else Block("text", [["The workflow calls no AI model."]])
    ]))  # fmt: skip
    out.append(Section("Subprocessors and data recipients", [
        Block("table", headers=["Kind", "Name", "Detail", "Where"],
              rows=[[s.kind, s.name, s.detail, "your own machines" if s.local else ""]
                    for s in rec.subprocessors])
        if rec.subprocessors else Block("text", [["Its runs send data to no outside service."]])
    ]))  # fmt: skip

    def pinned(t: Any) -> str:
        return ", ".join(p.name + (f" {p.fingerprint[:FINGERPRINT]}" if p.fingerprint else "") for p in t.pinned)

    def drift(t: Any) -> str:
        d = t.drift or {}
        parts = [f"new: {', '.join(d['added'])}"] if d.get("added") else []
        parts += [f"changed: {', '.join(d['changed'])}"] if d.get("changed") else []
        return "; ".join(parts)

    out.append(Section("Tools", [
        Block("table",
              headers=["Name", "Kind", "Address", "Pinning", "Pinned tools", "If a tool changes", "Changed since"],
              rows=[[t.name, t.label, t.address or "", t.pinning, pinned(t), t.on_tool_change or "", drift(t)]
                    for t in rec.tools])
        if rec.tools else Block("text", [["The workflow uses no tools."]])
    ]))  # fmt: skip
    guard = Section("Guardrails", [
        Block("table", headers=["Name", "Kind", "Detail"], rows=[[g.name, g.label, g.detail] for g in rec.guardrails])
        if rec.guardrails else Block("text", [["No safety step, agent add-on or budget."]])
    ])  # fmt: skip
    if rec.policies:
        guard.blocks.append(Block("table", title="Organisation policies in force",
                                  headers=["Policy", "Severity", "Says"],
                                  rows=[[p.name, p.severity, p.description] for p in rec.policies]))  # fmt: skip
    out.append(guard)
    oversight = Section("Human oversight", [
        Block("table", headers=["Step", "Kind", "What a person does", "Who decides"],
              rows=[[a.step, a.label, a.what, a.deciders] for a in rec.approvals])
        if rec.approvals else Block("text", [["No step asks a person."]])
    ])  # fmt: skip
    if rec.ungated:
        oversight.blocks.append(Block("text", [["Steps that act outside (write, send, run code) without a person "
                                                 "approving: " + ", ".join(rec.ungated)]]))  # fmt: skip
    out.append(oversight)

    ev = rec.evaluation
    evidence = Section("Evaluation evidence")
    g = ev.release_gate
    evidence.blocks.append(
        Block("fields", title=f"Release gate ({rec.environment})", rows=[
            ["Dataset", g.dataset + (f" (version {g.dataset_version})" if g.dataset_version else "")],
            ["Minimum scores", ", ".join(f"{k} {_num(x)}" for k, x in g.min_scores.items()) or "each evaluator's own"],
            ["Largest drop allowed", _pct(g.max_regression)], ["Evaluators", ", ".join(g.evaluators)],
        ]) if g else Block("text", [["No release gate."]], title=f"Release gate ({rec.environment})")
    )  # fmt: skip
    evidence.blocks += _experiment("Latest experiment", ev.experiment, "No finished experiment.")
    evidence.blocks += _experiment("Latest red-team run", ev.red_team, "No finished red-team run.")
    evidence.blocks.append(
        Block("table", title="Online evaluators (last 30 days)",
              headers=["Evaluator", "Share of runs scored", "Runs scored", "Mean score", "Pass rate", "Open alerts"],
              rows=[[o.evaluator, _pct(o.sampling), str(o.scored), _num(o.mean), _pct(o.pass_rate), str(o.open_alerts)]
                    for o in ev.online])
        if ev.online else Block("text", [["No online evaluator scores its runs."]], title="Online evaluators")
    )  # fmt: skip
    evidence.blocks.append(Block("text", [[f"Saved test cases: {ev.test_cases}."]]))
    out.append(evidence)

    out.append(Section("Publish history", [
        Block("table", headers=["When", "What", "Who", "Environment", "Version", "Detail"],
              rows=[[_when(h.at), h.action.replace("_", " "), h.who or "", h.environment or "",
                     f"v{h.version}" if h.version else "", h.detail] for h in rec.history])
        if rec.history else Block("text", [["No entry in the audit log."]])
    ]))  # fmt: skip

    d = rec.data_handling

    rows = [
        ["Conversations", f"deleted {d.conversation_days} days after their last activity" if d.conversation_days
         else "kept until deleted"],
        ["Audit log", f"kept {d.audit_days} days" if d.audit_days else "kept forever"],
        ["Long-term memories", f"deleted when not updated for {d.memory_days} days" if d.memory_days
         else "kept until deleted"],
        ["Waiting approvals", f"expire after {d.approval_expiry_days} days" if d.approval_expiry_days
         else "never expire"],
        ["Never in a trace", ", ".join(d.trace_redact)],
        ["Trace export", (f"{d.trace_export.name} ({d.trace_export.detail})" +
                          ("" if d.trace_content else ", without inputs and outputs")) if d.trace_export else "none"],
        ["AI notice", d.ai_notice], ["Share link", "on" if d.share_link else "off"],
        ["Answers marked as AI-generated", "yes (X-AI-Generated, generated_by)" if d.output_marked else "no"],
    ]  # fmt: skip
    out.append(Section("Retention and data handling", [Block("fields", rows)]))
    return out


def title(rec: SystemRecordOut) -> str:
    return f"AI-system record: {rec.workflow} ({rec.environment})"


def subtitle(rec: SystemRecordOut) -> str:
    return (f"{rec.organisation} / {rec.project} · version v{rec.version.semver} · "
            f"generated {_when(rec.generated_at)}")  # fmt: skip


# ------------------------------------------------------------------------------------------- Markdown ----
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~])")  # brackets escaped: no link or image can open


def md(text: str) -> str:
    """User text as Markdown that renders as itself, on one line: no link, image, HTML, heading or table break."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text).split()))


def markdown(rec: SystemRecordOut) -> str:
    out = [f"# {md(title(rec))}", "", md(subtitle(rec)), ""]
    for s in sections(rec):
        out += [f"## {md(s.title)}", ""]
        for b in s.blocks:
            if b.title:
                out += [f"### {md(b.title)}", ""]
            if b.kind == "fields":
                out += ["| | |", "|---|---|"] + [f"| **{md(k)}** | {md(v)} |" for k, v in b.rows] + [""]
            elif b.kind == "table":
                if not b.rows:
                    out += ["(none)", ""]
                    continue
                out += ["| " + " | ".join(md(h) for h in b.headers) + " |", "|" + "---|" * len(b.headers)]
                out += ["| " + " | ".join(md(c) for c in row) + " |" for row in b.rows] + [""]
            elif b.kind == "list":
                out += [f"- {md(row[0])}" for row in b.rows] + [""]
            else:
                out += [md(row[0]) for row in b.rows] + [""]
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- HTML ----
CSS = """
body{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 16px;color:#1a1a1a;background:#fff}
h1{font-size:1.6rem}h2{font-size:1.25rem;margin-top:2rem;border-bottom:1px solid #ddd}h3{font-size:1rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}th,td{border:1px solid #ddd;padding:4px 8px;
text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#f4f4f4}.sub{color:#555}
.fields th{width:28%}ul.notes li{color:#8a4b00}
@media print{body{margin:0}}
"""


def _h(text: str) -> str:
    return html.escape(str(text), quote=True)


def html_page(rec: SystemRecordOut) -> str:
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{_h(title(rec))}</title><style>{CSS}</style></head><body>",
           f"<h1>{_h(title(rec))}</h1>", f'<p class="sub">{_h(subtitle(rec))}</p>']  # fmt: skip
    for s in sections(rec):
        out.append(f"<h2>{_h(s.title)}</h2>")
        for b in s.blocks:
            if b.title:
                out.append(f"<h3>{_h(b.title)}</h3>")
            if b.kind == "fields":
                body = "".join(f"<tr><th>{_h(k)}</th><td>{_h(v)}</td></tr>" for k, v in b.rows)
                out.append(f'<table class="fields">{body}</table>')
            elif b.kind == "table":
                head = "".join(f"<th>{_h(x)}</th>" for x in b.headers)
                body = "".join("<tr>" + "".join(f"<td>{_h(c)}</td>" for c in row) + "</tr>" for row in b.rows)
                out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>" if b.rows else
                           "<p>(none)</p>")  # fmt: skip
            elif b.kind == "list":
                out.append('<ul class="notes">' + "".join(f"<li>{_h(row[0])}</li>" for row in b.rows) + "</ul>")
            else:
                out += [f"<p>{_h(row[0])}</p>" for row in b.rows]
    out.append("</body></html>")
    return "\n".join(out)


def render(rec: SystemRecordOut, fmt: Format) -> tuple[str, str, str]:
    """(body, media type, file name) of the record in `fmt`."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{rec.workflow}-{rec.environment}-v{rec.version.semver}").strip("-")
    if fmt == "html":
        return html_page(rec), "text/html; charset=utf-8", f"{slug}-system-record.html"
    return markdown(rec), "text/markdown; charset=utf-8", f"{slug}-system-record.md"
