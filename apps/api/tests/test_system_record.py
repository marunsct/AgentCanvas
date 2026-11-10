"""E-13: a deployment's AI-system record (purpose, owner, risk class...) and the evidence it gathers."""

from __future__ import annotations

from datetime import date, timedelta

from test_evaluators import new
from test_online import GREETS, call
from test_publish import make_greeter, set_greeting

from agentcanvas.db.models import AppUser, AuditLog, Membership
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.services import system_record
from agentcanvas.services.system_record import Evaluation, RecordFields, notes_for

HIGH = {"risk_class": "high", "risk_rationale": "screens job applicants", "purpose": "Sort applications"}


async def published(api) -> tuple[str, str, str]:
    wf = await make_greeter(api)
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return wf, pub["deployment"]["id"], pub["api_key"]


async def test_a_new_deployment_has_a_record_with_the_evidence_already_kept(api_client):
    wf, dep, _ = await published(api_client)
    r = await api_client.get(f"/v1/deployments/{dep}/system-record")
    assert r.status_code == 200, r.text
    rec = r.json()
    assert (rec["workflow"], rec["environment"], rec["version"]["semver"]) == ("Greeter", "prod", "1.0.0")
    assert rec["version"]["created_by"] == "dev@localhost"
    assert rec["changes"]["previous"] is None and rec["changes"]["summary"] == "the first version"
    assert rec["record"]["purpose"] == "" and rec["record"]["risk_class"] is None
    assert rec["notes"] == ["No purpose, owner or risk class is recorded yet."]
    assert [(h["action"], h["who"]) for h in rec["history"]] == [("publish", "dev@localhost")]
    handling = rec["data_handling"]
    assert handling["conversation_days"] == 90 and handling["audit_days"] == 400 and handling["output_marked"]
    assert not handling["share_link"] and handling["trace_export"] is None

    await set_greeting(api_client, wf, "Howdy")  # the next version names what changed since the one before
    await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    rec = (await api_client.get(f"/v1/deployments/{dep}/system-record")).json()
    assert rec["version"]["semver"] == "1.1.0" and rec["changes"]["previous"] == "1.0.0"
    assert rec["changes"]["summary"] == "1 step changed" and rec["changes"]["changed"] == ["greet"]


async def test_people_fill_in_the_record_and_the_audit_log_keeps_the_changes(app, api_client):
    _, dep, _ = await published(api_client)
    url = f"/v1/deployments/{dep}/system-record"
    review = (date.today() + timedelta(days=180)).isoformat()
    body = {
        "purpose": "  Greets visitors  ", "owner_id": "usr_dev", "risk_class": "limited",
        "risk_rationale": "talks to the public", "data_categories": ["public information", "public information"],
        "end_user_facing": True, "review_date": review,
    }  # fmt: skip
    r = await api_client.put(url, json=body)
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    assert rec["purpose"] == "Greets visitors" and rec["data_categories"] == ["public information"]
    assert (rec["owner_email"], rec["owner_member"]) == ("dev@localhost", True)
    assert rec["updated_by_email"] == "dev@localhost"
    assert rec["review_date"] == review and r.json()["notes"] == []
    assert (await api_client.get(url)).json()["record"] == rec  # kept

    async with app.state.db.sessionmaker() as s:
        from sqlalchemy import select

        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "update_system_record"))
        assert entry is not None and entry.target == f"dep_id={dep}"
        assert entry.detail["risk_class"] == "limited" and entry.detail["owner_id"] == "usr_dev"
        assert "purpose" in entry.detail["changed"] and entry.detail["review_date"] == review
    history = (await api_client.get(url)).json()["history"]
    assert [(h["action"], h["detail"]) for h in history][:1] == [("update_system_record", "risk class limited")]

    # clearing a field is writing the record without it
    r = await api_client.put(url, json={"purpose": "Greets visitors"})
    assert r.status_code == 200 and r.json()["record"]["risk_class"] is None and r.json()["record"]["owner_id"] is None


async def test_the_owner_must_be_a_member_and_a_risk_class_needs_a_rationale(app, api_client):
    _, dep, _ = await published(api_client)
    url = f"/v1/deployments/{dep}/system-record"
    r = await api_client.put(url, json={"owner_id": "usr_nobody"})
    assert r.status_code == 422 and "not a member" in r.text
    r = await api_client.put(url, json={"risk_class": "high"})
    assert r.status_code == 422 and "say why" in r.text
    assert (await api_client.put(url, json={"risk_class": "severe", "risk_rationale": "x"})).status_code == 422
    assert (await api_client.put(url, json={"data_categories": ["x" * 101]})).status_code == 422

    async with app.state.db.sessionmaker() as s:  # a member of another organisation is not a member here
        s.add(AppUser(id="usr_other", oidc_subject="other", email="other@example.com"))
        await s.flush()
        from agentcanvas.db.models import Organization

        s.add(Organization(id="org_other", name="Other"))
        await s.flush()
        s.add(Membership(org_id="org_other", user_id="usr_other", role="admin"))
        s.add(AppUser(id="usr_left", oidc_subject="left", email="left@example.com"))
        await s.flush()
        s.add(Membership(org_id="org_default", user_id="usr_left", role="builder"))
        await s.commit()
    assert (await api_client.put(url, json={"owner_id": "usr_other"})).status_code == 422
    assert (await api_client.put(url, json={"owner_id": "usr_left", "purpose": "p"})).status_code == 200
    async with app.state.db.sessionmaker() as s:  # the owner leaves
        await s.delete(await s.get(Membership, ("org_default", "usr_left")))
        await s.commit()
    rec = (await api_client.get(url)).json()
    assert rec["record"]["owner_member"] is False and rec["record"]["owner_email"] is None
    assert rec["notes"] == ["Its owner is no longer a member of the organisation: name a new owner."]


async def test_a_high_risk_system_without_evidence_or_approval_gets_notes_not_errors(app, api_client):
    wf, dep, key = await published(api_client)
    url = f"/v1/deployments/{dep}/system-record"
    r = await api_client.put(url, json={**HIGH, "review_date": (date.today() - timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    notes = r.json()["notes"]
    assert [n.split(":")[0] for n in notes] == [
        "Marked high risk, but there is no evaluation evidence",
        "Marked high risk, but no step asks a person",
        "Marked high risk without an owner",
        f"Its review date ({(date.today() - timedelta(days=1)).isoformat()}) has passed",
    ]
    # an online evaluator scoring its runs is evidence
    ev = await new(api_client, wf, "greets", "regex", GREETS)
    r = await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json={"evaluator_id": ev["id"]})
    assert r.status_code == 201, r.text
    await call(app, api_client, dep, key)
    rec = (await api_client.get(url)).json()
    assert [(o["evaluator"], o["scored"], o["mean"], o["pass_rate"]) for o in rec["evaluation"]["online"]] == [
        ("greets", 1, 1.0, 1.0)
    ]
    assert not any("evaluation evidence" in n for n in rec["notes"])
    assert any("no step asks a person" in n for n in rec["notes"])


async def test_a_release_gate_and_its_experiment_are_evidence(api_client):
    from test_gates import gated

    wf, _ = await gated(api_client, min_scores={"exact": 0.5})
    pub = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert pub.status_code == 200, pub.text
    dep = pub.json()["deployment"]["id"]
    ev = (await api_client.get(f"/v1/deployments/{dep}/system-record")).json()["evaluation"]
    assert ev["release_gate"]["min_scores"] == {"exact": 0.5}
    exp = ev["experiment"]
    assert exp["name"] == "release gate: prod" and exp["status"] == "done" and ev["red_team"] is None
    [target] = exp["targets"]
    assert target["pass_rate"] == 1.0 and target["evaluators"] == {"exact": 1.0}


def _rich() -> Workflow:
    return Workflow.model_validate({
        "id": "wf_rich", "name": "Rich",
        "state": {"channels": [{"name": "question"}]},
        "resources": [
            {"id": "r_m", "type": "model.chat", "name": "brain",
             "config": {"model": "anthropic:claude-sonnet-5-5", "fallbacks": ["openai:gpt-5.5"]}},
            {"id": "r_local", "type": "model.chat", "name": "local",
             "config": {"model": "ollama:llama3", "base_url": "http://user:pw@gpu.internal:11434/v1?x=1"}},
            {"id": "r_mcp", "type": "mcp.server", "name": "crm",
             "config": {"url": "https://mcp.example.com/mcp", "tool_filter": ["find"], "on_tool_change": "fail",
                        "discovered": [{"name": "find", "hash": "abc123"}, {"name": "delete", "hash": "def456"}]}},
            {"id": "r_http", "type": "tool.http", "name": "weather", "config": {"url": "https://api.weather.test/v1"}},
            {"id": "r_pii", "type": "mw.pii", "name": "mask", "config": {}},
        ],
        "nodes": [
            {"id": "n_g", "type": "guard.injection", "name": "screen", "config": {"on_fail": "stop"}},
            {"id": "n_a", "type": "core.agent", "name": "helper",
             "config": {"approve_tools": ["weather"], "approvers": {"roles": ["admin"], "not_initiator": True}}},
            {"id": "n_ok", "type": "core.approval", "name": "sign_off", "config": {}},
        ],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_g"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_g"}, "target": {"node": "n_a"}},
            {"id": "e3", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "n_ok"}},
            {"id": "e4", "kind": "flow", "source": {"node": "n_ok"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_a", "port": "model"}},
            {"id": "w2", "kind": "wiring", "source": {"node": "r_mcp"}, "target": {"node": "n_a", "port": "tools"}},
            {"id": "w3", "kind": "wiring", "source": {"node": "r_http"}, "target": {"node": "n_a", "port": "tools"}},
            {"id": "w4", "kind": "wiring", "source": {"node": "r_pii"},
             "target": {"node": "n_a", "port": "middleware"}},
            {"id": "w5", "kind": "wiring", "source": {"node": "r_local"}, "target": {"node": "n_g", "port": "model"}},
        ],
        "settings": {"budget": {"max_cost_usd": 0.5}},
    })  # fmt: skip


def test_the_record_reads_models_tools_guardrails_and_approvals_from_the_version():
    wf, registry = _rich(), default_registry()
    models = system_record.models_of(wf, None, registry)
    assert [(m.block, m.model, m.provider_label, m.role, m.address, m.used_by) for m in models] == [
        ("brain", "anthropic:claude-sonnet-5-5", "Anthropic", "model", None, ["helper"]),
        ("brain", "openai:gpt-5.5", "OpenAI", "backup", None, ["helper"]),
        # the address without its credentials, path or query
        ("local", "ollama:llama3", "Ollama (local)", "model", "http://gpu.internal:11434", ["screen"]),
    ]
    tools = {t.name: t for t in system_record.tools_of(wf, _dep(), registry)}
    crm = tools["crm"]
    assert crm.address == "https://mcp.example.com" and crm.pinning == "pinned" and crm.on_tool_change == "fail"
    assert [(p.name, p.fingerprint) for p in crm.pinned] == [("find", "abc123")]  # only what the workflow may call
    assert tools["weather"].address == "https://api.weather.test"
    assert [(g.name, g.detail) for g in system_record.guardrails_of(wf, registry)] == [
        ("screen", "on failure: stop"),
        ("mask", ""),
        ("budget", "max cost usd 0.5"),
    ]
    approvals = system_record.approvals_of(wf, registry)
    assert [(a.step, a.what, a.deciders) for a in approvals] == [
        ("helper", "approves the tools weather before they run", "roles admin; not the person who started the run"),
        ("sign_off", "approves before the flow goes on", "anyone who answers the Inbox or the caller"),
    ]


def test_an_app_connector_browsed_before_fingerprints_is_pinned_by_name_only():
    wf = _rich()
    wf.resources[2].config["discovered"] = [{"name": "find"}]
    [crm] = [t for t in system_record.tools_of(wf, _dep(), default_registry()) if t.name == "crm"]
    assert crm.pinning == "names only"
    wf.resources[2].config = {"url": "https://mcp.example.com/mcp"}
    [crm] = [t for t in system_record.tools_of(wf, _dep(), default_registry()) if t.name == "crm"]
    assert crm.pinning == "not pinned" and crm.pinned == []


def test_notes_are_reminders_only_for_what_is_missing():
    today = date(2026, 11, 11)
    high = RecordFields.model_validate({**HIGH, "owner_id": "usr_dev"})
    assert notes_for(high, Evaluation(test_cases=3), [], True, today, "prod")[0].startswith(
        "Marked high risk, but there is no evaluation evidence"
    )  # test cases alone are not evaluation evidence
    limited = RecordFields(risk_class="limited", risk_rationale="chat", review_date=today)
    assert notes_for(limited, Evaluation(), [], True, today, "prod") == []  # the review date is today: not passed


def _dep():
    from agentcanvas.db.models import Deployment

    return Deployment(id="dep_x", workflow_id="wf_rich", version_id="ver_x", environment="prod")


async def test_subprocessors_name_every_service_its_runs_send_data_to(app, api_client):
    from agentcanvas.db.models import WorkflowRow, WorkflowVersion
    from agentcanvas.services.deployments import DeploymentService

    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Rich"})).json()["id"]
    ir = _rich().model_dump(mode="json") | {"id": wf}
    assert (await api_client.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": "1"})).status_code == 200
    async with app.state.db.sessionmaker() as s:  # published as is (a model key is not needed to read the record)
        row = await s.get(WorkflowRow, wf)
        assert row is not None
        version = WorkflowVersion(id="ver_rich", workflow_id=wf, semver="1.0.0", ir=row.draft_ir, layout={},
                                  ir_hash=row.ir_hash, created_by="usr_dev")  # fmt: skip
        s.add(version)
        await s.commit()
        dep, _ = await DeploymentService(s).publish(row, "prod", version, created_by="usr_dev")
        dep_id = dep.id
    rec = (await api_client.get(f"/v1/deployments/{dep_id}/system-record")).json()
    assert [(r["kind"], r["name"], r["detail"], r["local"]) for r in rec["subprocessors"]] == [
        ("model provider", "Anthropic", "anthropic:claude-sonnet-5-5", False),
        ("model provider", "OpenAI", "openai:gpt-5.5", False),
        ("model provider", "Ollama (local) at http://gpu.internal:11434", "ollama:llama3", True),
        ("app connector", "https://mcp.example.com", "crm", False),
        ("web API", "https://api.weather.test", "weather", False),
    ]
    assert [a["step"] for a in rec["approvals"]] == ["helper", "sign_off"] and rec["ungated"] == []
    assert [g["name"] for g in rec["guardrails"]] == ["screen", "mask", "budget"]


EVIL = "Greets <script>alert(1)</script> [click](https://evil.test) | ![x](https://evil.test/p.png)\n# heading"


async def test_the_record_downloads_as_markdown_or_html_with_user_text_escaped(app, api_client):
    _, dep, _ = await published(api_client)
    url = f"/v1/deployments/{dep}/system-record"
    assert (await api_client.put(url, json={"purpose": EVIL, "risk_class": "limited", "risk_rationale": "chat",
                                            "owner_id": "usr_dev"})).status_code == 200  # fmt: skip
    r = await api_client.get(f"{url}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == 'attachment; filename="Greeter-prod-v1.0.0-system-record.md"'
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    text = r.text
    assert text.startswith("# AI-system record: Greeter (prod)")
    assert "## Summary" in text and "## Evaluation evidence" in text and "## Publish history" in text
    assert "| **Risk class** | Limited: chat |" in text and "| **Owner** | dev@localhost |" in text
    # nothing the person wrote becomes HTML, a link, an image, a heading or a new table cell
    purpose = next(line for line in text.splitlines() if line.startswith("| **Purpose**"))
    assert "<script>" not in purpose and "](" not in purpose.replace("\\](", "") and purpose.count(" | ") == 1
    assert "\\<script\\>" in purpose and "\\[click\\](https:" in purpose and "\\# heading" in purpose

    r = await api_client.get(f"{url}/export", params={"format": "html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].endswith('-system-record.html"')
    assert "<script>" not in r.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "<h2>Human oversight</h2>" in r.text and "<p>No step asks a person.</p>" in r.text
    assert (await api_client.get(f"{url}/export", params={"format": "pdf"})).status_code == 422

    async with app.state.db.sessionmaker() as s:  # each download is in the audit log
        from sqlalchemy import select

        found = (await s.scalars(select(AuditLog).where(AuditLog.action == "export_system_record"))).all()
        assert [(e.target, e.detail["format"], e.actor_id) for e in found] == [
            (f"dep_id={dep}", "markdown", "usr_dev"), (f"dep_id={dep}", "html", "usr_dev"),
        ]  # fmt: skip


def test_markdown_escaping_keeps_text_on_one_line_and_inert():
    from agentcanvas.services.system_record_doc import md

    assert md("a|b\n\n<b>*x*</b> `c` [l](u)") == "a\\|b \\<b\\>\\*x\\*\\</b\\> \\`c\\` \\[l\\](u)"
