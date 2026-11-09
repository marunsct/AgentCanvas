"""Audit forwarding (E-3): which audit entries count as security events and leave the server."""

from fastapi.routing import iter_route_contexts

from agentcanvas.api.audit import SKIPPED
from agentcanvas.api.main import create_app
from agentcanvas.services.audit_sink import SECURITY_ACTIONS
from agentcanvas.settings import Settings

# writes that are audited but are not security events: building, testing and running workflows, content. A new
# route that changes something must be added here or to SECURITY_ACTIONS on purpose (the test below fails until then)
ROUTINE = {
    # workflows, versions, canvases and their code
    "create_workflow", "replace_workflow", "rename_workflow", "duplicate_workflow", "apply_commands", "create_version",
    "restore_version", "import_file", "import_agent_spec", "refactor_inline", "refactor_to_subgraph", "pull",
    "finetune_export",
    # projects and their files, defaults and first-run setup
    "create_project", "rename_project", "save_file", "delete_file", "move_file", "new_script", "upload_file",
    "put_copilot_settings", "put_knowledge_defaults", "put_model_defaults", "put_setup", "skip_setup", "test_model",
    "server_models", "test_trace_export", "index_runs",
    # running, conversations, feedback (approvals are security events, above)
    "start_run", "cancel_run", "continue_thread", "fork_thread", "delete_thread", "api_feedback", "api_upload",
    "share_feedback", "share_upload", "share_close_conversation", "a2a_endpoint", "mcp_endpoint", "send_event",
    "delete_trigger", "chat", "chat_stream", "make_workflow",
    # evaluation: datasets, test cases, evaluators, experiments, annotation, red team
    "create_dataset", "update_dataset", "delete_dataset", "add_examples", "add_runs", "add_test_cases",
    "import_examples", "update_example", "delete_example", "pull_from_langsmith", "push_to_langsmith",
    "add_test_case", "delete_test_case", "run_test_cases", "test_case_from_run", "create_evaluator",
    "update_evaluator", "delete_evaluator", "try_evaluator", "start_experiment", "cancel_experiment",
    "delete_experiment", "add_online", "update_online", "delete_online", "create_queue", "update_queue",
    "delete_queue", "add_items", "remove_item", "annotate", "start_red_team",
    # knowledge bases
    "create_knowledge_base", "update_knowledge_base", "delete_knowledge_base", "add_text", "add_url", "add_sitemap",
    "upload_documents", "delete_document", "reindex_knowledge_base", "recrawl_now", "set_recrawl", "search_knowledge",
    # the Copilot, presence, the person's own preferences and invitations, memory items
    "send_message", "stop_turn", "code_help", "decide_proposal", "choose_chat_model", "new_chat", "open_thread",
    "rename_thread", "heartbeat", "leave", "put_preferences", "decline_invitation", "delete_item",
    # starting an OAuth sign-in (the connection itself is made by `callback`)
    "authorize",
    # Matura's own feed of changes (its rows are audited by what they change)
    "matura_event",
}  # fmt: skip


def _audited_writes() -> set[str]:
    """Every route whose successful calls `api/audit.record` writes to the audit log."""
    app = create_app(Settings(env="test", database_url="sqlite:///:memory:"))
    return {
        r.name
        for r in iter_route_contexts(app.routes)
        if r.methods and r.methods - {"GET", "HEAD", "OPTIONS"} and (r.path or "").startswith("/v1/")
        if r.name is not None and r.name not in SKIPPED
    }


def test_every_audited_write_is_classified_as_a_security_event_or_routine():
    """The list of security actions was written before the kill switch, share links, OAuth clients and git pushes
    existed, and missed them. Every write route now has to be one or the other."""
    writes = _audited_writes()
    unclassified = writes - SECURITY_ACTIONS - ROUTINE
    assert not unclassified, f"add to SECURITY_ACTIONS (services/audit_sink.py) or ROUTINE here: {sorted(unclassified)}"
    assert not SECURITY_ACTIONS & ROUTINE, SECURITY_ACTIONS & ROUTINE
    assert not ROUTINE - writes, f"no such route any more: {sorted(ROUTINE - writes)}"
    # the security routes named in the review, each one classified as a security event
    assert {
        "stop_all_runs", "update_share", "regenerate_share", "put_client", "delete_client", "delete_connection",
        "put_sandbox", "delete_project", "push_to_github", "create_trigger", "erase", "accept_invitation",
        "create_token", "revoke_org_token", "create_scim_token", "set_member", "create_policy", "put_org_settings",
        "put_secret", "rotate_key",
    } <= SECURITY_ACTIONS  # fmt: skip


def test_security_actions_name_routes_or_the_rows_services_write():
    """A misspelt action would never match a row: each is a route name or one SCIM and sign-in groups write."""
    written_by_services = {
        "scim_create_user", "scim_set_active", "scim_delete_user", "scim_role_sync", "scim_create_group",
        "scim_replace_group", "scim_patch_group", "scim_delete_group", "sso_role_sync", "sso_member_removed",
    }  # fmt: skip
    assert SECURITY_ACTIONS - written_by_services <= _audited_writes()
