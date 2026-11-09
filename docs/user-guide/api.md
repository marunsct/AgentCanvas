# API reference

Everything the web app does goes through the same HTTP API, and you can call it too: from scripts, CI, or your own
application. This page covers what every call has in common. The endpoints for running a published workflow are in
[Publish and integrate](publish.md#call-a-deployment), API tokens in
[Organisations, roles and policies](accounts.md#api-tokens).

## The full reference

The API is described by an OpenAPI 3.1 document, the reference of every endpoint, its parameters, its answers and
its errors. Get the one of the server you call, so it matches its release:

| Where | What |
|---|---|
| `GET /v1/openapi.json` | The server's OpenAPI document, for anyone signed in (a person or an API token), production included |
| `/docs` and `/openapi.json` | The interactive reference and the document without signing in, outside production only: production servers turn them off, so they do not map the API for anonymous callers |
| `packages/ir-schema/openapi.json` in the repository | The document of each release, as committed |

```bash
curl -H "Authorization: Bearer act_…" https://<your AgentCanvas>/api/v1/openapi.json -o agentcanvas-openapi.json
```

Open the file in any OpenAPI viewer (Swagger Editor, Redocly, Postman, Insomnia) or generate a client from it
(openapi-generator, orval, openapi-typescript). Settings › You › About shows which release a server runs.

## Addresses and sign-in

- **Base address.** Through the web server (every production setup) the API is under `/api/`:
  `https://<your AgentCanvas>/api/v1/...`. A server reached directly (development) answers at `/v1/...`.
- **Sign-in.** Send `Authorization: Bearer <token>`: a personal API token (`act_…`, made under Settings › You ›
  API tokens, with a role no higher than yours) or, from a browser, the identity provider's access token. A published
  workflow's endpoints take its deployment key instead ([Publish and integrate](publish.md#call-a-deployment)).
- **Request ids.** Every answer has an `x-request-id` header. Send your own `x-request-id` to tie a call to your logs;
  quote it when you report a problem.
- **Saving a workflow** needs `If-Match` with the version you edited (from the `ETag` of the read): without it the
  answer is `428`, with an older version `409`.

## Errors

Every error answers with a problem document ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)), content type
`application/problem+json`; the OpenAPI document calls its schema `Problem`:

```json
{"type": "https://agentcanvas.dev/errors/invalid", "title": "Invalid request", "status": 422,
 "detail": "1 invalid field(s)", "errors": [{"loc": "body.name", "msg": "Input should be a valid string"}]}
```

| Field | Meaning |
|---|---|
| `type` | The kind of error, stable: match on it rather than on `title` or `detail`. It identifies the kind; it is not a page to open |
| `title` | A short summary of the kind |
| `status` | The HTTP status |
| `detail` | What went wrong with this request, in plain words (may change between releases) |
| `errors` | With a `422` for invalid fields: each field's place as a dotted path (`loc`) and the message (`msg`) |
| `request_id` | With a `500`: the request id to quote |
| `retry_after` | With a `429`: the seconds to wait, also in the `Retry-After` header |

| `type` ends with | Status | When |
|---|---|---|
| `/errors/invalid` | 422 | A field is missing or invalid, or holds something the database cannot store |
| `/errors/validation` | 422 | The workflow has errors, listed in the document (publishing, running) |
| `/errors/unauthorized` | 401 | No sign-in, or the token is not valid any more |
| `/errors/forbidden` | 403 | Your role cannot do this (the detail names the role it needs) |
| `/errors/not-found` | 404 | It does not exist, or not in an organisation you belong to |
| `/errors/conflict` | 409 | It conflicts with the current state (an older version, a name already used) |
| `/errors/connection-required` | 409 | An app connection must be signed in first |
| `/errors/precondition-required` | 428 | `If-Match` is missing |
| `/errors/rate-limited` | 429 | Too many calls: wait `Retry-After` seconds |
| `/errors/upstream` | 502 | A model provider or another service the call needed failed |
| `/errors/unavailable` | 503 | Something the call needs is not available right now: try again later |
| `/errors/internal` | 500 | A bug on the server: quote `request_id` |

Other statuses (`404` for an unknown address, `405`) have `type` `about:blank`.

## Versioning

The API's version is in its addresses: `/v1`. Within `/v1` changes only add: new endpoints, new optional request
fields, new fields in answers, new error `type`s. Write clients that ignore fields they do not know. An endpoint that
is to go away is first marked `deprecated` in the OpenAPI document and announced in the changelog, and is removed
no earlier than the release after. A change that cannot be made by adding would come as `/v2`, served alongside
`/v1` for at least one release.
