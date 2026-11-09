# Spike SP-2 — LangSmith Sandboxes as the script runner

| | |
|---|---|
| Sprint | S7 (time-boxed, 1 point) |
| Question | Can workspace code (script nodes, custom nodes, event handlers) run in a LangSmith Sandbox, with secrets injected by the auth proxy so they never enter the sandbox? |
| Outcome | **Yes, behind a setting.** `AGENTCANVAS_SCRIPT_EXECUTION=langsmith` is implemented and tested against a fake client. It has not yet run against the live service, because this environment has no LangSmith credentials. Latency and the open questions below need one session with a real key. |
| Code | `apps/api/agentcanvas/sandbox/langsmith.py`, `sandbox/policy.py`, `sandbox/runners.py` (`SandboxRunner`), `tests/test_sandbox.py` |

## 1. The SDK surface (langsmith 0.14.1, verified by inspection)

- `AsyncSandboxClient(api_endpoint=None, api_key=None, …)`. The key defaults to `LANGSMITH_API_KEY` and the endpoint to `LANGSMITH_ENDPOINT`.
- `create_sandbox(snapshot=…, name=…, idle_ttl_seconds=…, delete_after_stop_seconds=…, mem_bytes=…, proxy_config=…)` returns an `AsyncSandbox`. It waits until the sandbox is ready. Both TTLs must be multiples of 60.
- `update_sandbox(name, proxy_config=…)` patches the proxy in place. Access-control changes apply immediately. Size (`vcpus`, `mem_bytes`, `fs_capacity_bytes`) cannot be updated.
- The `AsyncSandbox` methods are:
  - `write(path, bytes|str)`
  - `read(path)`
  - `run(command, timeout=int, cwd=…, env=…)`, which returns `ExecutionResult(stdout, stderr, exit_code)` or raises `CommandTimeoutError`
- `delete_sandbox(name)` and `aclose()`.
- `proxy_config` has three parts:
  - `rules`: header rules shaped `{name, match_hosts, match_paths, headers: [{name, type, value}]}`. The header `type` is `workspace_secret` (`{NAME}`), `plaintext` or `opaque`. `opaque` is write-only and encrypted at rest. Rules can also be of type `aws` or `gcp`.
  - `access_control`: `{allow_list: [...]}` (default-deny) or `{deny_list: [...]}`.
  - `callbacks`.
- **Default egress is allow-all** when there is no `access_control`. This confirms review item V-11c. Organisations on restricted egress have their allow-list replaced by the org policy.

## 2. Design

```
SandboxRunner (one per project, from the ProjectRegistry)
  ├─ subprocess / docker → a child process per call (unchanged)
  └─ langsmith → LangSmithBox (one sandbox per project, created lazily)
        create_sandbox(snapshot, mem_bytes, idle TTL, proxy_config = policy + secrets)
        write /sandbox/child.py                     (the same stdlib child as the local modes)
        write /sandbox/ws.tgz + tar -x into /ws     (only when a workspace file changed)
        per call: write /sandbox/in-<id>.json; run `python3 -I /sandbox/child.py IMPORT < in.json` in /ws
```

- **One warm sandbox per project, not one per call.**
  - Creating a sandbox boots a VM and waits until it is ready. That cost is acceptable once, but not on every node execution.
  - Calls reuse the sandbox and differ only by their input file.
  - The workspace is uploaded as a single archive, which takes one `write` and one `run`. It is re-uploaded only when the files' mtimes change.
- **Same child protocol in every mode.** `child.py` is stdlib-only. It prints the result as the last line on stdout and sends user prints to stderr, so result parsing (`_parse`) is shared by every mode.
- **Secrets never enter the sandbox.**
  - Header values in the policy reference project secrets as `{{ secret.NAME }}`.
  - Those values are sent to LangSmith as `opaque` headers. The proxy injects them into matching HTTPS requests from outside the sandbox.
  - The sandbox receives no environment variables from us. The test asserts that neither the uploaded files nor the environment contain the value.
- **Policy or secret change → `update_sandbox`; size change → a new sandbox.** The proxy config is fingerprinted, including secret values, without logging them.
- **Offline by default.** An empty `egress` sends `access_control.allow_list = []`. Hosts named in `inject` rules are added on port 443, because an allow-list must include the hosts that rules target.
- **No leaks after a crash.** Sandboxes stop after `AGENTCANVAS_LANGSMITH_IDLE_S` (600 s) of inactivity and are deleted 600 s later. `ProjectRegistry.aclose()` deletes them at API shutdown.

## 3. Sandbox policy (FR-X-08, basic)

The policy is the file `sandbox.json` at the workspace root. It is versioned like the rest of the code:

```json
{
  "egress": ["pypi.org:443"],
  "inject": [{"name": "crm", "hosts": ["api.crm.example.com"],
              "headers": {"Authorization": "Bearer {{ secret.CRM_TOKEN }}"}}],
  "timeout_s": 30,
  "memory_mb": 512
}
```

- **Limits apply in every sandboxed mode.** `timeout_s` and `memory_mb` are enforced as follows:
  - subprocess: rlimits, plus the host-side timeout
  - docker: `--memory`
  - langsmith: `mem_bytes` and the `run` timeout
- **Egress and injection need the langsmith mode.** The local sandboxes have no network at all.
- **Checks:** the file editor and `/workspace/catalog` report these problems:

  | Code | Problem |
  |---|---|
  | E082 | The policy file is invalid. |
  | E083 | A header uses a secret the project does not have. |
  | W082 | Egress or inject rules are set, but the execution mode cannot apply them. |
  | W074 | Egress allows every host. |
- **Workspace UI:** the Workspace page has a *Sandbox policy* button that scaffolds the file.

## 4. Open questions (need a LangSmith key; about 1 hour)

1. Is `allow_list: []` accepted as "no egress"? If it is refused, send a single unroutable entry instead, such as `offline.invalid:1`.
2. Latency: measure a cold `create_sandbox` from a snapshot, a warm `run` round trip, and the upload of a 1 MB workspace. The target for a warm script node is under 300 ms of overhead.
3. Can one sandbox run several commands at once (parallel `core.map` branches)? If not, keep a small pool per project.
4. Is `python3` present in the default snapshot? Recommendation: build a project snapshot from `python:3.12-slim` plus the workspace's locked dependencies, keyed by the `uv.lock` hash (spec 05 §4), and set `AGENTCANVAS_LANGSMITH_SNAPSHOT`.
5. With restricted egress, does the org allow-list replace or intersect a sandbox's `access_control`? The docs say it replaces it. Our "offline by default" then does not hold, so the UI should show a warning.

## 5. Recommendation

- Keep **docker** as the self-hosted default and make **langsmith** the hosted-edition default for scripts and event handlers once questions 1–3 are answered.
- Follow-ups for the GA backlog:
  - snapshot per `uv.lock` hash
  - a pool when concurrent calls need it
  - `workspace_secret` references for secrets that live only in LangSmith
  - reuse the same policy for Deep Agent sandbox backends (`backend.sandbox`, where W074 applies)
