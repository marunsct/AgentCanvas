"""The OpenAPI document describes the errors the API really sends: RFC 9457 problem documents (ADR 0009), not FastAPI's
default `HTTPValidationError` ({detail: [{loc: [...], msg, type}]}), so a client generated from it parses them."""

from __future__ import annotations

import jsonschema

PROBLEM = "application/problem+json"


def _operations(doc: dict):
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method in ("get", "put", "post", "patch", "delete"):
                yield f"{method.upper()} {path}", op


def _schema(doc: dict, ref: str) -> dict:
    """A component schema with its `$ref`s resolvable (jsonschema reads them from the document's root)."""
    name = ref.rsplit("/", 1)[1]
    return {**doc["components"]["schemas"][name], "components": doc["components"]}


def test_every_operation_declares_problem_documents_for_its_errors(app):
    doc = app.openapi()
    schemas = doc["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and "ValidationError" not in schemas
    assert schemas["Problem"]["required"] == ["type", "title", "status", "detail"]
    ref = {"$ref": "#/components/schemas/Problem"}
    with_body = 0
    for name, op in _operations(doc):
        responses = op["responses"]
        assert responses["default"]["content"] == {PROBLEM: {"schema": ref}}, name  # any error, whatever its status
        if "422" in responses:
            with_body += 1
            assert responses["422"]["content"] == {PROBLEM: {"schema": ref}}, name
    assert with_body > 100  # the routes that take parameters or a body


async def test_an_invalid_request_matches_the_declared_schema(app, api_client):
    doc = app.openapi()
    declared = doc["paths"]["/v1/projects/{project_id}/workflows"]["post"]["responses"]["422"]
    r = await api_client.post("/v1/projects/prj_default/workflows", json={"name": 5})
    assert r.status_code == 422 and r.headers["content-type"] == PROBLEM
    body = r.json()
    assert body["errors"] and isinstance(body["errors"][0]["loc"], str)
    jsonschema.validate(body, _schema(doc, declared["content"][PROBLEM]["schema"]["$ref"]))

    missing = await api_client.get("/v1/projects/prj_nope/workflows/wf_nope")  # a status no route lists: `default`
    assert missing.status_code == 404
    jsonschema.validate(missing.json(), _schema(doc, "#/components/schemas/Problem"))

    from agentcanvas.errors import WorkflowHasErrors
    from agentcanvas.validation.engine import Diagnostic

    diagnostic = Diagnostic(code="E001", severity="error", message="the workflow has no start", node_id="n_1")
    exc = WorkflowHasErrors("workflow has 1 error(s)", errors=[diagnostic.model_dump()])  # its errors are not fields
    body = {"type": exc.type_, "title": exc.title, "status": exc.status, "detail": exc.detail, **exc.extra}
    jsonschema.validate(body, _schema(doc, "#/components/schemas/Problem"))
