# Changelog

All notable changes to AgentCanvas. Versions follow [semver](https://semver.org); dates are UTC.

## Unreleased

The product pass ([PLAN.md](PLAN.md): one app shell, Settings, the Copilot), the hardening passes and the deep
review of 2026-10-29 ([HARDENING.md](HARDENING.md)); no version has been tagged for them yet.

### Security
- Dependencies: pyasn1 0.6.5 and pypdf 6.21.4, which fix denial-of-service bugs (CVE-2026-119194 to 119197, CVE-2026-119203 to 119205) the image scan reported.
- Removing someone from an organisation now also deletes their API tokens and app connector connections there
  (suspending still keeps them, refused until reactivated).
- A project secret's value is now limited to 64 KB.
- Client credentials an admin enters for a sign-in server are now used only for the app connector servers they were
  entered with (enter the same client id with another server's address to add it), so another connector that names the
  same sign-in server cannot receive tokens made with them; Connect asks before sending you to a sign-in server on
  another host.
- A connector set to act as the service account now uses the organisation's service connection only in runs no person
  starts (schedules, webhooks, deployments, share links), a deployment's runs resumed from the inbox, and admins' own
  runs; a builder's draft run is told to publish the workflow and run it there.
- A published deployment's A2A agent card (and SCIM's Location) now gives the address callers can use behind the web
  server: AGENTCANVAS_PUBLIC_URL + /api when set, otherwise the forwarded scheme and /api prefix; the web image's nginx
  passes on https from a trusted proxy in front of it.
- Runs started with a deployment key, a share link or a webhook now accept only files uploaded for that deployment (or
  by the same share-link visitor), not the project's other files; a schedule's own input still uses any of the project's
  files.
- Listing conversations with a cursor from another organisation now answers "unknown cursor", and a thread id already in
  use elsewhere is refused without saying where.
- Unpublishing a deployment now switches its share link off and replaces it, and ends the visitors' conversations, so
  publishing again does not bring back the old link.
- LLM, pairwise and red-team judges are told that the workflow's input and output are data to grade, and an output can
  no longer pose as part of the judge's instructions.
- In docker and remote script execution, sandbox.json egress entries "*" and "~regex" (which only LangSmith understands)
  are now reported as error E085 and refused at run time instead of silently allowing nothing; W074 is reported for
  LangSmith only.
- The sandbox egress proxy no longer passes a second request pipelined behind a plain-HTTP one to the upstream
  unchecked; each connection carries one request and its body.
- The HTTP tool, the hosted edition's model and MCP clients, knowledge-page fetching and the sandbox egress proxy now
  refuse NAT64, IPv4-compatible, 6to4 and Teredo forms of private and cloud-metadata addresses.
- Usage events (which carry a failed backup model's error text) now have project secret values masked like every other
  run event.
- The HTTP tool reads an answer only up to its response limit, so a very large or endless response no longer fills the
  server's memory.
- An event handler that starts (or resumes) a run whose code emits the same event no longer loops for ever: like emitted
  events, such chains stop after 5 levels with an error in the event log.
- In docker script execution, a cancelled or timed-out call can no longer leave its sandbox container running.
- A sandboxed script that writes without end is stopped once its result passes 32 MB, and only the last 64 KB of what it
  printed is kept, so it cannot fill the server's memory.
- Stopping a sandboxed script that had filled its output pipe no longer hangs the call: what is left in the pipes is
  read and dropped while the stopped script is reaped.
- In the hosted edition, OpenAI and Azure OpenAI models and embedders always connect through the public-address client,
  and other providers (and backups, embedders, Ollama's local default) may not set an address of their own.
- A rotated project secret now reaches the next run at once; cached graphs kept the model clients and MCP tools built
  with the old value.
- Browsers no longer offer to fill a saved password into a new secret's value.
- Several requests refused at once no longer each renew the sign-in, which could end the session and force a full sign-
  in; a token that has already expired is renewed before the request is sent.
- In the Helm chart, workspace code in the sandbox can no longer reach the cluster's DNS; the API passes it the egress
  proxy's address (`AGENTCANVAS_EGRESS_PROXY_RESOLVE`).
- The Helm chart trusts X-Forwarded-For from every RFC 1918 range by default, and the web image's `TRUSTED_PROXIES`
  takes several ranges.
- Single sign-on groups: on a server with several organisations, any organisation's groups table could enrol every person who signed in (an admin maps a claim every token carries, such as the issuer) and then list their names and addresses. A table now changes only its own members' and invitees' roles; adding others needs a single-organisation server or `AGENTCANVAS_SSO_GROUPS_ADD_MEMBERS=true`. The sync also no longer locks the organisation on requests where nothing changes.
- SCIM: another organisation's directory could change the address of a person invited here who had not signed in yet, because an invitation is one user row for every organisation; whoever owned the new address then joined with this organisation's role. A pending user who belongs to another organisation can no longer be renamed or re-addressed through SCIM (409).
- An error raised before a framework adapter could handle it (building the graph, reaching an MCP server, setting up
  a model client) went into the run's error event and the server log with any key in its text; it is masked now, as
  the adapter's own errors are (HARDENING R-7).
- A secret that a provider's error echoed base64- or URL-encoded (or JSON-escaped) was not masked, and a secret
  under 6 characters was never masked at all. Every encoded form is masked now, in run events, traces and error
  messages, and a secret needs at least 6 characters (HARDENING S-9).
- The egress proxy forwarded a sandbox's own `Host` header (reaching another site on an allowed address) and hop-by-
  hop headers, accepted a request framed both by `Content-Length` and `Transfer-Encoding`, and honoured a call's
  credential for a minute past its time limit. It now names the checked host, drops hop-by-hop headers, refuses
  double framing, and each credential lives for its call (time limit plus the wait for a free sandbox) and names the
  call in the proxy's log. A sandbox busy for a minute answers 503, reported as "every sandbox was busy" (HARDENING
  S-7).
- A deployment key could tell a thread id of another deployment or organisation from one that does not exist (404
  for unknown, 422 "not started by this deployment" otherwise). Both are now the same 404. nginx's per-visitor rate
  limit now covers each deployment's MCP and A2A endpoints (and its agent card) and the Matura webhook, as it did
  the deployment API and share links (HARDENING S-6).
- In the hosted edition, an MCP server's address was checked by name and then connected to by name again, so a DNS
  answer that changed in between (rebinding) could reach the cloud metadata service or the cluster; a model's own
  base URL was not checked at all. MCP clients and OpenAI-compatible model clients there now resolve, check and
  connect to the same public address (HARDENING S-5).
- **Calls of different projects ran side by side as one user in the sandbox service**, so one call's code could read
  another's files under `/tmp`, and a process it left running could watch the next call. Each sandbox container now
  runs one call at a time and, after it, stops everything the code started (a daemon that forked away included) and
  removes what it wrote; more calls at once come from more containers (`SANDBOX_REPLICAS`, 4 by default in
  production, `sandbox.replicas` in Helm) (HARDENING S-2).
- **One call's workspace code could read another's egress credential in a shared sandbox**: the signed proxy address
  was in each child's environment, readable through `/proc/<pid>/environ` by any process of the same user. It now
  reaches the child on stdin, and the child marks itself not dumpable, so other processes cannot read its
  environment or memory (HARDENING S-2, part 1).
- **A staging or production server ran workspace code as the API's own user** when left at the default `inprocess`
  mode (or `subprocess`), with the database password and keys in reach; it only logged a warning. It now refuses to
  start that way: use the sandbox service (`remote`), `docker` or `langsmith`, or set
  `AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS=true` when only trusted people edit workspaces (HARDENING S-8).

- **A rotated API key kept being used until the server restarted**: model clients and MCP tool sets were cached by
  the secret's name, not its value, so after replacing a leaked key, runs went on sending the old one. A changed
  secret value now builds new clients, and an MCP server's tools are loaded again with it (HARDENING R-3).
- **A builder could read the project's stored model keys by pointing a model at a server they run**: the model-list
  probe, the connection test and runs sent the provider key (a project secret or server variable) to whatever base
  URL was given. A stored key now goes only to its provider or the server saved with it; any other address gets a
  key only when it is typed in or named on purpose as the model's API key (HARDENING S-4).
- **Workspace code in the sandbox service could call the API from inside**, past nginx and its per-visitor limits:
  on the compose stacks the sandbox shared a network with the API, the API believed any `X-Forwarded-For`, and the
  sandbox's in-process network block can be side-stepped by code that imports `_socket`. The API now calls the
  sandbox on its own internal network (`sandbox-control`) and refuses every request from it
  (`AGENTCANVAS_REFUSE_CLIENTS`); it trusts forwarded headers only from the web container's network (`front`); the
  subnets are `SANDBOX_CONTROL_SUBNET` and `FRONT_SUBNET` if they clash with yours (HARDENING S-3).
- **A project could open the sandbox to the cloud metadata service or the database by naming the address in
  `sandbox.json`**: the egress proxy refused private addresses a host name resolved to, but let any address the list
  named literally through, and the list is written by the project. Private addresses now need the operator
  (`AGENTCANVAS_EGRESS_ALLOW_PRIVATE=1` on the proxy, `egress_allow_private` for the local Docker proxy), link-local
  and metadata addresses are never reached, and a list naming one is refused (HARDENING S-1).

### Fixed
- The web image starts with several `TRUSTED_PROXIES` ranges (the Helm chart's default lists three); nginx refused to start with "invalid number of the geo parameters".
- A request that conflicts with a change saved at the same moment now gets a 409 Conflict asking to retry, instead of an
  internal error.
- When one organisation's data key cannot be unwrapped (for example after changing AGENTCANVAS_SECRET_KEY without
  keeping the old master key), the API now starts, logs an error for that organisation and keeps trying, instead of
  refusing to start.
- The retention job now deletes all expired conversations in a tick (a batch at a time, within a time budget) instead of
  at most 500 per organisation per hour, replicas share the work, and a conversation continued since the scan is kept.
- Saving two project or organisation settings at the same moment (for example Copilot settings and knowledge defaults)
  no longer loses one of the changes.
- Reviewers and viewers can now connect (and disconnect) their own sign-in to an app connector, so a reviewer can resume
  a run with their own connection.
- Importing OpenAI Agents SDK code keeps the imports, constants and helpers its tools use, so the tools load and run;
  tools without a docstring, with a run-context parameter, or run with agent.run_sync() import too.
- CrewAI, Google ADK, OpenAI Agents, Mastra and the other flow frameworks no longer duplicate chat messages that a step
  sends back with their ids, and honour message removals.
- Pushing a Mastra flow to GitHub writes a CI check that runs its tests with Node.js, so the pull request's check no
  longer fails.
- A dated model id such as gpt-5-mini-2025-08-07 no longer gets a temperature its model refuses.
- Imported path conditions whose text spans several lines now work; a comparison with an infinite number is listed in
  the import report instead of failing at run time.
- Imported Langflow, Flowise, n8n, Agent Spec and Agents SDK files no longer arrive with duplicate or reserved step and
  block names.
- Searching past conversations no longer finds experiment and test runs after you index older runs, and the "not indexed
  yet" count is right on large projects.
- The fine-tuning export no longer writes conversations with unanswered tool calls, or ones cut short when the run was
  recorded; the preview lists them as left out, with the reason.
- Searching a large knowledge base no longer holds up other requests on a server without pgvector, and semantic search
  is faster.
- Exporting a workflow with a large knowledge base, and searching many large knowledge bases on one server, takes far
  less memory.
- A web page you added to a knowledge base by hand is no longer removed when a sitemap that also listed it stops listing
  it.
- A knowledge base re-crawl where one page cannot be updated reports that page and carries on with the others, instead
  of failing with a server error.
- Knowledge bases built from web pages keep the text of pages that leave out </head> (common in minified HTML) or never
  close a navigation menu.
- Experiment results show how many examples each evaluator could not score, and a release gate no longer passes when
  some examples went unscored. An online alert stays open while the evaluator fails to score live runs.
- Copilot test runs and dry runs no longer call remote A2A or MCP agent steps; they record what would have been sent. A
  test run also asks before using AI models that sit inside a subflow.
- The Copilot runs at most 8 tools from one reply and tests the workflow at most 6 times per message, so a runaway
  answer cannot run up model costs.
- Accepting a Copilot proposal no longer overwrites workspace files you edited after the Copilot proposed its change; it
  says which files changed and writes none.
- When you accept a Copilot proposal while the Copilot is still answering, the new proposal keeps the file changes it
  made on top of the accepted one.
- A release gate no longer counts a simulated-user experiment as its evaluation; it runs the examples itself.
- Knowledge bases chunked by markdown headings keep fenced code blocks whole: a # comment inside one no longer starts a
  new section.
- Red-team runs with model-written attacks no longer count an injection or jailbreak that never asked for the canary as
  held; the built-in attacks take its place.
- The retrieval evaluator matching by id now finds passages whose id has upper-case letters.
- An LLM judge that replies with a score outside 0 to 1 (for example 7 on a 1-10 scale) or with true/false no longer
  counts as a perfect pass; the example is left unscored with the reason.
- Starting a run that fails on a database constraint other than a just-created thread no longer reports the misleading
  "thread already has an active run".
- After a restart (or on another replica) a run's secrets refresh and security check now use the workflow's own project
  and organisation settings, not the default project's.
- A long run's oldest events in memory now have long texts in step outputs and tool answers shortened, not only token
  deltas, and trimming no longer slows down as the run grows.
- A run whose full record could not be saved still records its tokens and cost, so budgets and spending reports count
  it.
- Cancelling a run the instant it starts no longer leaves its conversation stuck as running.
- Reading text from pictures no longer raises the image-size safety limit for the whole server; each page of a multi-
  page picture is size-checked and read in turn.
- Debug runs with mocked models now search the project's knowledge bases and read its files like normal runs, instead of
  losing them.
- Runs that fail, are cancelled or hit their time budget now record the tokens and cost of the model calls they made
  before stopping.
- An Inbox answer whose run was cancelled or stopped by a restart before it took, or a question the run asks again,
  shows in the Inbox again instead of staying decided.
- A shared chat link no longer loses the end of an answer when the connection closes right after it, or behind a proxy
  that rewrites line endings.
- In the Inbox a decided item disappears straight away instead of staying clickable, deciding one item no longer blocks
  the others, and an error shows on the item it belongs to.
- Scrolling up in the Copilot to read an earlier message is no longer undone by the answer streaming in; sending a
  message follows the answer again.
- Double-clicking Send in the Copilot no longer starts the same request twice.
- Leaving the Playground while an answer is being written now stops it instead of letting the model run on unseen.
- A page that crashed no longer stays on screen after moving to another page, the top bar stays usable, and a page that
  could not be downloaded after an update says so and offers Reload.
- Queued and cancelled experiments no longer show a red status badge; only a failed one does.
- A running experiment's results no longer flash "Loading results…" every second; they refresh in place every few
  seconds.
- After the server refuses an edit, the save badge says "Not saved" (hover for the reason) instead of "Saved".
- Undo and redo pressed while an edit is still being saved now happen once the save lands, instead of being ignored.
- Edits made in the moment the editor reloads after a conflicting or refused edit are no longer lost, and undo waits
  until the reload is done.
- Save as test case now shows why saving failed, and a double-click saves one test case.
- Edits typed into a checkpoint's state in the Timeline are no longer wiped when a run ends and the history reloads.
- A run left streaming in a background tab keeps updating (approvals, the result) instead of waiting until the tab is
  shown again.
- Opening a past run from the Runs tab while a run is streaming no longer mixes the live run's steps into it: the rows
  wait until the run ends, and a thread opened any other way stops the old stream.
- Forking from a checkpoint starts from a clean canvas instead of keeping the earlier run's node colours, streamed text
  and costs.
- A reviewer approving or rejecting a step is no longer refused because Dry run was ticked or pins were left in the
  browser; the debug options are shown only to builders.
- Double-clicking Approve, Reject, Continue or Fork no longer sends the decision twice and shows a false conflict error;
  the buttons wait until the run has started.
- Git sync: a workflow connected to a repository's root (no folder) opened a pull request that deleted every other file of the repository, and a folder link removed other workflows' sub-folders. A push now records the files it writes in `.agentcanvas-files` and removes only files an earlier push wrote.
- An exported project whose workflow has an app connector or MCP server no longer fails its own smoke test on a machine that cannot reach that server: the test builds the graph with a recorded tool in its place.
- The API no longer stops at start-up when neither `AGENTCANVAS_PUBLIC_URL` nor a CORS origin is set (the production compose files set neither): signing in to an app connector then says to set the public address.
- Accessibility, found by an automated scan of every page in the light and dark themes: text and status colours that were too faint now meet WCAG AA contrast, links inside text are underlined, the audit log's rows open with a keyboard button, the Problems panel can be scrolled with the keyboard, and code line numbers are readable.
- The block Name and Label fields in the inspector now follow undo and redo instead of showing what was typed before.
- A workflow's day and month USD budgets now count what runs on every server have spent so far, so runs started
  together on several replicas can no longer pass the limit by whole runs. (HARDENING G-8).
- Runs paused for a person or for a remote A2A agent's question can be answered on another replica or after a
  restart; the remote agent's replies are now kept in the database. (HARDENING G-9).
- The agent network template could not be exported as an Open Agent Spec file that other runtimes accept: it starts
  three steps at once, and an Agent Spec flow's start takes one path. Steps that run side by side and meet at one
  "Wait for all" are now written one after another, and the export's notes say so.
- Opening a conversation (its state, history or Explain) failed with a server error while one of the workflow's MCP
  servers was down, because reading built the whole graph and contacted every server first. Reading now goes on
  without that server's tools (HARDENING R-5).
- Smaller run-time fixes: the version a published conversation continues on is read by the order of its runs, not
  their start time (SQLite keeps that to the second); the security check before a run is repeated when the workspace
  code changes, not only when the workflow does; the child-process sandbox's network guard covers UDP too (HARDENING
  R-11).
- An event handler with a dedup key that failed (a timeout, a service down) never saw that event again: its
  redelivery was skipped as a duplicate. The key is now kept only when the handler finished, so a redelivered event
  reaches it once more (HARDENING R-10).
- A run, Copilot answer, share-page reply or Playground stream that went quiet during a long step (a slow tool, a
  model thinking) was cut by proxies with a 60-second read timeout. Every stream now sends a keep-alive comment
  after 15 quiet seconds, which readers ignore (HARDENING R-9).
- With many runs going at once, a run that had just finished was forgotten straight away (running runs counted
  against the finished ones a server keeps), so a client attaching to it a moment later got 404. A run going on for
  hours no longer keeps every streamed token in memory: past 64 MB its oldest text pieces are emptied, and a server
  restart no longer leaves waiting tasks behind (HARDENING R-8).
- Two servers starting the same new caller-named conversation at once answered the second with a server error; it is
  the same 409 ("already has an active run") as two starts on one server (HARDENING R-6).
- CrewAI, Google ADK, Mastra, OpenAI Agents SDK, the flow-engine frameworks and single-agent exports wrote a model
  block's temperature into the code even for models that refuse it (current Claude and GPT-5 class models), so the
  exported project's first call failed with a 400. The temperature is now written only for models that take it.
- CrewAI, Google ADK, Mastra, Pydantic AI, Agno, Strands, LlamaIndex and Agent Framework exports dropped backup
  models, the answer cache and step policies with only a general line in the README, and the Agent Spec exports did
  not mention them, nor fields that collect values or approvals that no longer wait for a yes. Each export now has a
  GAPS.md naming every block concerned, and an approval with steps after it is a "Must fix" in the Agent Spec
  exports. (HARDENING X-6).
- An exported service's Dockerfile and compose files used floating images (python:3.12-slim, postgres:16-alpine,
  caddy:2-alpine) and resolved its Python packages afresh on every build. Images are now pinned by version and
  digest, `make setup` writes uv.lock, and the image installs exactly what it locks. (HARDENING X-7).
- An Agent Spec file that wrote its start node out in place (under start_node and again in nodes) failed to import
  with "a control flow edge points at a step that is not in the flow", and the import report only counted the agent
  tools it left out. Such files now import, and the report names each tool. (HARDENING X-9).
- In LangGraph code, a resource named `<router>_route` or `<map>_fan_out` was silently replaced by the router's or
  map's own helper, so the agent got the helper as its tool. The helpers now have names no block can take.
  (HARDENING X-3).
- A key typed straight into a header or environment value (Authorization: Bearer …) was written into exported code
  and the exported copy of the workflow. The canvas now warns about it (W021) and every export refuses it, naming
  the block and the header, until it is stored as a project secret. (HARDENING X-1).
- An Agent Spec file whose branch values held a quote or a backslash imported as a router with an invalid rule, and
  agent instructions with Windows line breaks came out of every single-agent export with plain line breaks. Branch
  values are now quoted properly both ways, and instructions are written exactly as typed. (HARDENING X-5).
- CrewAI and Google ADK code changed multi-line Set values, Ask a person prompts and knowledge search queries by
  indenting the lines inside them. Those texts are now written exactly as typed, and every Python generator is
  checked against a corpus of awkward texts. (HARDENING X-2).
- Opening a deployment's share settings created its share link as a side effect, so merely looking made a link.
  Reading the settings now creates nothing; the link is made when they are first saved, for example by switching
  sharing on.
- A builder could move a project into or out of a Matura workspace, although only admins may rename or delete a
  project. Moving it now needs an admin too.
- With sign-in, a request naming an organisation other than the default made it the default, a database write on
  every request of two clients or tabs working in different organisations. The choice is now written at most once a
  minute per organisation. (HARDENING F-4).
- Deleting a project removed its workspace folder on the server's main loop, so a large workspace paused every other
  request and stream while it went. It is now removed in the background thread pool. (HARDENING F-4).
- Every workflow read and validation walked every file of the project's workspace to see whether its code had
  changed. The answer is now kept for a second, and a change saved through AgentCanvas is seen at once. (HARDENING
  F-4).
- The experiments list, refreshed every second while one runs, read every result of every experiment, outputs
  included, one query per experiment, and an experiment's results came back all at once. The list now reads only
  what its summaries need in one query and shows 50 experiments a page, and results come 1000 at a time (the
  evaluation page fetches them all). (HARDENING F-3).
- A deployment's score chart read up to 100,000 score rows and grouped them on the server, and stopped counting past
  that. The database now groups them by hour or day, so every score counts and the page loads faster. (HARDENING
  F-3).
- Saving a version read every Copilot conversation of the workflow, messages and all, to credit accepted proposals.
  It now reads only the proposals of conversations changed since the version before. (HARDENING F-3).
- A workflow's contract page looked up each deployment's version with a query of its own. It now reads them all in
  one query. (HARDENING F-3).
- The knowledge page's list ran one count query per knowledge base. It now counts them all in one query. (HARDENING
  F-3).
- Two admins deleting an organisation's last two projects at the same moment could leave it with none; restoring a
  version could keep its steps but not their positions; a template whose kit failed partway left a workflow with
  half its kit; and a review whose correction the dataset refused was saved anyway. Now one project always stays, a
  restore writes steps and positions together, a failed kit leaves no workflow behind, and a refused correction
  saves no review. (HARDENING C-12).
- On SQLite, an audit-log search from a time with a UTC offset was off by that offset; asking for dataset version 0
  returned the latest version; paging a project's event log with another project's event id answered as if it
  existed; and `%` or `_` in the audit target filter matched everything. Times now compare as the same instant,
  version 0 is not found, a foreign event id is unknown, and the target filter matches its text literally.
  (HARDENING C-11).
- A cron trigger whose stored schedule could not be read stopped all scheduled runs of the server. That trigger is
  now skipped with a warning in the log, and the others keep firing. (HARDENING C-10).
- Making a workflow's code folder, recording a version's workspace commit and saving a release gate ran git on the
  server's main loop and outside the workspace's lock, so a code save at the same moment could stay uncommitted and
  a gate could reuse an evaluation of older code. They now take turns with saves, off the main loop. (HARDENING
  C-9).
- Uploading several files to a knowledge base when a later one failed (the same text twice, too many chunks,
  unreadable) kept the earlier ones, so a retry was refused as a duplicate. Now a failing file adds none of them,
  and the retry works. (HARDENING C-8).
- Unpublishing a deployment and publishing it again silently brought its old API key back to life, without showing
  it. Publishing again now issues a new key, shown once, and the old key stays revoked. (HARDENING C-7).
- When a release gate's evaluation took longer than its ten-minute limit, publishing failed but the experiment kept
  running and spending on models. It is now cancelled when the gate gives up. (HARDENING C-6).
- Running a workflow's saved test cases, or publishing it, kept a database connection for the whole run, so a few at
  once could leave every other request waiting. The connection now goes back to the pool while the cases run.
  (HARDENING C-5).
- Importing a CSV file with a very long cell (over 128 KB) failed with a server error. Such files now import, and a
  file the CSV reader cannot parse is refused with a message that says so. (HARDENING C-4).
- Exporting a dataset whose name has characters such as Japanese failed with a server error. The download now
  carries an ASCII name for every browser and the real name for those that read it; workflow, agent and bridge
  exports are named the same way. (HARDENING C-3).
- Saving a version, or publishing, twice at the same moment could fail with a server error because both tried to
  take the same version number. Now the second one sees the first: a snapshot is told nothing changed, and a publish
  pins the same version. (HARDENING C-2).
- An answer whose run failed before it took (the model provider down, say) took the question out of the Inbox
  although the run was still waiting for it; it now goes back. The Echo demo engine dropped a paused run's state
  before running the continuation, so a continuation that failed could not be answered again (HARDENING R-4).
- The Evaluation guide's table of evaluators left out the retrieval evaluator and did not say what a composite does
  with a part that cannot score. It now lists retrieval and explains that such a part is left out of a weighted mix
  but counts as failed for "all" and "any". (HARDENING D-7).
- An uploaded PDF, Word or HTML file was embedded with its metadata title but searched and cited by its file name,
  so its search results changed after the next reindex; and adding a run to an annotation queue by hand showed it as
  unreviewed even when it had reviews. Files are now embedded with the title they are cited by, and re-added runs
  show their reviews. (HARDENING E-12).
- An experiment whose target workflow no longer validated stayed "running" and was retried by every server every few
  seconds; experiment costs left out the AI judges entirely, a judge model without a price counted as free with no
  warning, and a drift alert never resolved once traffic stopped. Such an experiment now ends with an error,
  experiments show the judges' cost beside the runs' cost and name unpriced judge models (online scores say so in
  their comment), and an alert resolves when its window has too few scores. (HARDENING E-11).
- When checking the Copilot's proposal failed, its answer stopped without an error or an end and the editor showed
  only its generic reload notice; a large workflow was cut mid-text before the Copilot saw it; and accepting a
  proposal with tests or files that do not exist silently ignored them. The answer now always ends with the error
  and its end, a large workflow loses whole blocks with a note saying how many are shown, and wrong test or file
  choices are refused like wrong changes. (HARDENING E-10).
- Importing a dataset from CSV turned every reference cell into JSON ("1", "true", "1e5" became numbers and
  booleans), so exact-match evaluators failed against the run's text output, and a "null" cell became an empty
  reference value. References now take the type of the workflow output they are compared with and stay text
  otherwise; a null in a typed column means no reference. (HARDENING E-8).
- When the AI judge did not answer in JSON, any stray 0 or 1 in its reply was taken as the score ("lists 1 item then
  fails badly" passed with 1.0), a rating like "8/10" gave no score, and a reply with two JSON objects failed to
  parse. The judge's last JSON answer is used now; otherwise only a labelled score, a rating such as 8/10 or 80%, or
  a lone decimal counts, and anything else scores nothing with the reason. (HARDENING E-7).
- Accepting the Copilot's open proposal while it was still answering left its next proposal holding the accepted
  changes a second time, so the proposal no longer applied ("the workflow changed since") and everything it added in
  that answer was lost. The next proposal now keeps only what was added after the accepted one. (HARDENING E-6).
- A "Wait for all" block fed by steps that follow different paths of one "Choose a path" block validated clean, yet
  only one path ever runs, so the run stopped without reaching Finish and without any message. Validation now
  reports it (E016), also when the steps are several blocks after the router. (HARDENING E-5).
- A loop that went through a "Repeat for each item" block was warned about as a loop with no way out, although the
  block leaves the loop when its list is empty. Such loops are no longer warned about. (HARDENING E-4).
- A "Choose a path" block whose fallback path name was cleared got two errors, a bad name and a path '' with no
  arrow. Only the bad name is reported now. (HARDENING E-4).
- A prompt that mentioned "state.something" in plain text, or a template calling state.get(...), was reported as
  reading an unknown data field. Only the {{ … }} and {% … %} parts of prompts and templates (and whole conditions)
  are checked now, and method calls are not taken for fields. (HARDENING E-4).
- Clearing an optional output field of a block (such as a guard's flag output or Search knowledge's sources) gave
  the error "refers to unknown state channel ''". An empty optional field now means "not saved" and is not reported;
  an empty required one still is. (HARDENING E-4).
- A Search knowledge block's minimum score was compared with raw scores whose scale depended on the mode, so in
  hybrid mode (the default) any value of 0.033 or more found nothing. Scores and the minimum are now 0 to 1 in every
  mode (keywords relative to the best match, meaning the cosine, hybrid 1 for first in both rankings), and the guide
  explains each. (HARDENING E-3).
- After a knowledge base's embedding model was changed, searches compared the old vectors with a question embedded
  by the new model and returned noise until someone pressed Reindex, and exports shipped the old vectors under the
  new model's name. A stale knowledge base is now searched by keywords only (the page says so), exports ship it
  without vectors, and vectors of different lengths are never compared. (HARDENING E-2).
- After an undo or redo that changed a workflow's budget, the Budget fields kept showing the old numbers. Now they
  show the budget as it is, while still keeping a number that is half typed.
- The cron trigger form did not say which time zone a schedule uses, so "0 9 * * *" could be read as 09:00 local
  time. Now the field and each cron trigger say UTC. (HARDENING C-10).
- Deleting a test case or a trigger, or removing a release gate or an online evaluator, happened at the first click
  and a failure was not shown. Now each asks first and says when the server refused; deleting a block, which can be
  undone, says how to undo it. (HARDENING W-13).
- In Simple mode, clicking a port offered every block, including the advanced ones Simple mode hides. Now it offers
  the same blocks as the Simple palette. (HARDENING W-12).
- On a shared chat page, one malformed event in the reply stream ended the reply there. Now that event is skipped
  and the rest of the reply arrives. (HARDENING W-12).
- One knowledge document whose stored web address could not be read took the whole Knowledge page to an error
  screen. Now the address is shown as text, and only http and https addresses are links. (HARDENING W-12).
- Pasting while looking inside a subgraph or at a Copilot proposal added the blocks to the top level of the
  workflow, where they could not be seen. Now paste says to leave that view first. (HARDENING W-11).
- An undo or redo step left the history before it was saved, so a step the server refused was lost. Now a step
  leaves the history only once it is saved. (HARDENING W-11).
- When someone else saved the workflow while a Copilot proposal was being accepted, its edits were applied on the
  latest version but the proposal stayed open, and accepting it again failed. Now the proposal is marked accepted
  once its edits are saved. (HARDENING W-11).
- Selecting a block whose settings have defaults saved the workflow and added a step to undo, because the settings
  form reported its defaults as an edit. Now only what the person changes is saved. (HARDENING W-11).
- A block or channel name that was not allowed, or already taken, was silently dropped when the field lost focus,
  and renaming a channel left the old name in prompts and conditions. Now the field says what is wrong and keeps the
  text for fixing, and after a channel rename the State panel offers to update the blocks that still use the old
  name. (HARDENING W-10).
- On a large workflow the canvas redrew every block several times a second while a run streamed, because each status
  or token gave every block new data. Now only the blocks whose content changed are redrawn, and the default layout
  and problem counts are computed once per change instead of once per block. (HARDENING W-9).
- On a phone the beginner guide opened off screen, where it could not be read or closed, so it opened again on every
  visit to an empty workflow; the guided build box could run past the screen too. Now both dock at the bottom of a
  small screen and can be closed. (HARDENING W-8).
- Pressing Enter to pick characters in a Japanese, Chinese or Korean input method sent the Copilot message half-
  typed, the More menu could not be used with the arrow keys, and the command palette's Enter could run a command
  below the twelve it shows. Now Enter during composition only picks characters, the More menu takes the focus and
  moves with the arrow keys (Escape gives the focus back), and the palette's cursor stays on the commands it shows.
  (HARDENING W-7).
- Removing a path from a "Choose a path" block left its arrow in the workflow with nothing to leave from, invisible
  and impossible to delete, and the Problems dock could not point at it. Now the arrow follows a renamed path and
  goes with a removed one, and an arrow's problem in the Problems dock shows the arrow and offers "Remove this
  connection". (HARDENING W-6).
- After one workflow failed to open, every workflow opened after it showed "Could not load workflow" until a reload,
  and switching quickly between two workflows could show the first one. Now the error clears on the next open and a
  late answer for a workflow left behind is ignored. (HARDENING W-5).
- A double-click on Run or Send started two server runs, and one on Home's "Create empty" or "Start the guided
  build" created two workflows. Now the second click is ignored while the first is on its way. (HARDENING W-4).
- In an MCP server's tool list, unticking one tool while all were offered left only that tool offered. Now unticking
  removes just that tool, ticking the last missing one offers all again, and the last offered tool cannot be
  unticked. (HARDENING W-3).
- When the connection to a run's event stream dropped (an API restart, a proxy timeout), the editor stayed on
  "Running…" with Run, chat and Stop dead until a reload. Now the run panel says the connection was lost and the run
  can be started again. (HARDENING W-2).
- When the server refused an edit, the editor threw away the whole undo/redo history and any edits typed while it
  was being saved. Now only the refused edit is dropped, the notice names it, and the later edits are kept and
  saved. (HARDENING W-1).

- Exported LangGraph projects build their model clients with the same sampling rule as the server (the rule and the
  catalog file it reads are bundled with the export), so an export of a workflow on a current Claude or GPT-5 model
  does not send them a temperature either.
- **Templates failed on their first model call with a Claude or GPT-5 key**: every template set `temperature: 0`,
  kept when your project's model is swapped in, and current Claude models (Opus 5.5, Sonnet 5.5) refuse a non-
  default temperature with a 400, while GPT-5 takes only the default. The catalog now says which models take
  sampling settings; runs leave temperature, top_p and top_k out for those that do not, templates no longer set
  them, and the editor warns (W020) where one is set. Local models were never affected, which is why it went
  unnoticed.
- **The Copilot charged prompt-cache reads at full price**, so its token limit ended a chat after one or two builds
  and the cost it showed was five to ten times the bill: it re-sends its knowledge pack on every call, and Anthropic
  serves that from the cache at a tenth of the price. Costs now price cache reads and writes at their own rates (the
  catalog has them for the Claude models; `AGENTCANVAS_MODEL_PRICES` takes `[in, out, cache read, cache write]`),
  workflow runs included, and the limit counts what was sent and received less what came from the cache (HARDENING
  E-1).
- **Workspace code kept running after its run was cancelled**: a cancelled run, or one that ran out of its time
  budget, left its script process (or Docker container) running with nobody waiting for it, and the sandbox service
  let a call its caller had dropped run to its time limit. The code now stops with the run, on every sandbox mode
  (HARDENING R-2).
- **Clicking Cancel twice (or a server restart, or another server taking a stalled run over) could leave a
  conversation stuck as "running"**: the second stop landed while the run was saving its outcome, cut the save
  short, and the next message on that conversation was refused with 409 for a minute. A run that is saving itself is
  no longer stopped again, and whoever waits for it is always told it is over (HARDENING R-1).
- **Importing an n8n file with a loop of pass-through nodes stopped the whole server**: the importer followed the
  loop forever on the event loop, so every other request waited too. Both walks now stop at a node they have seen,
  and imports run off the event loop. A fuzz test now wires every sample file into loops (HARDENING C-1).
- **A damaged group of steps broke the editor's blast-radius overlay** (a server error) instead of being skipped:
  validation already reports it (E018) and nothing runs it; the overlay and the dry run now leave it out, as the
  other readers do. Found by the workflow fuzz test on the new Parallel code review template.
- **Workspace code that emitted an event failed in the sandbox**, the default for Docker and servers:
  `agentcanvas.sdk.emit` looked for the run in the sandbox's own process, found none, and failed the step
  ("Called get_config outside of a runnable context"). The sandboxed code now records its events, they come back
  with its result, and the API emits them into the run, so they show on the canvas and reach the project's
  handlers as they did in-process.
- **Every "changed … ago" was off by the viewer's time zone on SQLite** (the local edition without Docker): SQLite
  keeps no time zone, the API sent `2026-10-29T02:31:47`, and browsers read that as local time, so a workflow
  changed a minute ago showed as "9 hours ago" in Tokyo. Every timestamp column now reads back in UTC with its
  zone (`UtcDateTime`); Postgres already did.
- **A local embedding model could not be chosen for knowledge, and every model choice offered models that could
  not answer**: Settings › Models › By purpose took free text with the chat catalogue as suggestions, so a local
  server's models never appeared and a chat model could be saved as the embedding model. Every choice (judges,
  simulated users, knowledge embeddings, the Copilot's menu) now lists only models set up to answer
  (`GET /v1/projects/{id}/chat-models` and `/embedding-models`): hosted ones with their key, what a running local
  server lists, and, for embeddings, only models that embed. A knowledge base can switch its embedding model under
  **Add documents › Embed with**. An LM Studio, vLLM or llama.cpp embedding model now works (it was sent OpenAI's
  token ids, which those servers refuse), and one on the project model's server uses its address and key.
- **The Copilot's model menu offered models that could not answer**: it listed the whole catalogue, so picking a
  provider without a key (or a local server that was not running) failed on the next message. The menu now lists
  only models set up to answer (`GET /v1/workflows/{id}/copilot/models`: hosted ones whose key is a project secret
  or server variable, what a local server lists right now, the project's OpenAI-compatible model, each through the
  Copilot's privacy and policy checks), ends with **Set up more models…**, and names a model outside the catalogue
  without its provider prefix.
- **The Copilot panel's header ran out of the panel**: a long model name pushed **New chat** off the side panel's
  edge. The model chip now gives way. A table in an answer keeps its words whole and scrolls sideways instead of
  breaking them letter by letter.
- **A menu opened near the window's right edge ran off screen** (the Copilot's model menu); it now moves left
  until it fits.
- **No model had a price, so costs read $0 and USD budgets and cost alerts never fired**: the catalog carried no
  prices at all (W015 warned on every cost budget, with a remedy no user could apply). It now prices the Claude
  models (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 4.5 $1/$5 per million tokens), and
  `AGENTCANVAS_MODEL_PRICES` prices any other catalog model; a malformed table stops the server from starting.
- The Deep Agents Copilot under-counted tokens from providers that do not name their model in a reply (Ollama,
  OpenAI-compatible servers): LangChain's usage handler skips those replies. The token cap and cost now count them.
- **A workflow of another project opened with this project's blocks, models and settings**: the Inbox is
  organisation-wide, so "see the run" (or a pasted link) could open a workflow the app's current project does not
  hold, and its custom blocks showed as unknown. The editor now switches to the workflow's project and loads again
  (once; when the project cannot be chosen, it stays as before).
- On a laptop window (under ~1320 px) the top bar hid Events, Settings and the person menu past its right edge. The
  page links now become icons there (the open page keeps its name; names show on hover and stay for screen readers).
- On a 1024-px laptop window the editor's bar pushed ▶ Test, Copilot and the person menu out of sight (the bar
  scrolls without a scrollbar). Below 1180 px the framework badges give way and a long workflow name is shortened.
- **Runs listed conversations by id, not by time**: a thread named by its caller (`support-42`) sorted above or below
  everything else whatever its age. `GET /threads` now orders by when each conversation began; its cursor is still
  the last id, and an unknown one is refused (422) instead of returning an arbitrary page.
- Running a workflow with no steps failed inside the engine with `ValueError: Graph must have an entrypoint`, and
  left a failed conversation behind. It is now refused before anything is recorded: "this workflow has no steps yet".
- **SQLite did not enforce the schema's cascades**: a deleted workflow's published endpoint kept answering with its
  old key, and its versions, deployments, threads and runs stayed in the database. App connections now turn on
  `PRAGMA foreign_keys`; migrations keep their own engine, where a batch table rebuild must not cascade.
- Saving the Copilot form erased a separate model's server address and key set through the API: the settings PUT
  now merges (an explicit null clears), and the form has fields for both. "Use the workflow's model" is now "Use
  the project's model", which is what it did.
- The MCP endpoint refuses a browser `Origin` other than its own or a configured CORS origin (403) and an
  `MCP-Protocol-Version` it does not speak (400), as the Streamable HTTP transport requires.
- The Copilot took one turn at a time per conversation with a set in each API process's memory, which two
  replicas did not share: the turn now holds a lease on the conversation row (`busy_until`, 15 minutes at most).
- The Workspace dropped unsaved edits when another file was opened: it now asks (Discard changes / Keep editing),
  and the browser warns before the tab closes; a failed delete says why.
- The Runs page could crash when the API answered with something of another shape (found by the chaos sweep).
- Deleting a workflow that had been published and called failed on Postgres (references to its versions and
  deployments had no delete rule). A migration adds the rules.
- **A chat stopped after its first message.** The run's input was kept as the caller's own dict, the engine turned its
  message dicts into message objects in place, and saving the run's recording then failed, so the thread stayed
  "running" and every next message answered "already has an active run". The input is now a plain copy, and a run
  whose full save fails still records its status and frees its thread (`_finish_bare`).
- A client that sends its next message the moment it reads `RUN_FINISHED` could get "already has an active run": the
  run published its last event before it saved itself and released the thread. The last events now come after.
- **Concurrent migrations.** Replicas that start together each ran Alembic; three of four crashed on a duplicate-type
  error. Migrations now take a Postgres advisory lock (the others wait, then find nothing to do) and give up on a
  table lock after 60 s instead of queuing every other query behind an `ALTER TABLE`.
- The Agent Spec export, the bridges list and the export plan answered 500 for a router, Set step or HTTP tool whose
  free-form settings had the wrong type (a hand-edited or imported document). They read them defensively now.
- The exported **Ask your documents** template failed its own tests because the export script did not ship the
  knowledge base the template searches; the script now includes the sample handbook, as the API's export does.
- The web app waited for ever on a server that never answered (no timeout on plain requests): they now end after two
  minutes with a message. A streamed run's last event is delivered even when the connection closes without a blank line.
- **Two query values answered with a 500**: a page cursor or dataset version too large for a 64-bit integer (SQLite
  raises `OverflowError`, not `DataError`), and a NUL character in a memory namespace on Postgres (LangGraph's store
  raises psycopg's `DataError`, not SQLAlchemy's). Both are 422 now, and the audit cursor is bounded.
- **A misrouted `/api` crashed every page** when a proxy answered an API path with the app's own page (HTTP 200, HTML): the
  client handed the HTML to components as data ("`data.map is not a function`"). It is now an error that says so ("the server
  answered with a web page instead of data. Is /api forwarded to the AgentCanvas API?"). A page that still receives data
  of an unexpected shape (an old page against a newer server) shows its crash notice with a hint to reload.
- **Failed requests waited a second for nothing**: a 404, 403 or 422 was retried once; now only a dropped connection or a
  server error is.
- **A sign-in token with an empty or blank `sub` was accepted** (PyJWT only checks that the claim exists), so every such token
  would have shared one account. It is refused with a 401.
- **An export of a workflow with an emoji in its name or description could not be installed**: the description was
  written into `pyproject.toml` with `json.dumps`, which spells a character outside the Basic Multilingual Plane as a
  surrogate pair that TOML rejects. All six packagers (LangGraph, the flow frameworks, Google ADK, CrewAI, OpenAI Agents,
  agent designs) now use `toml_string`.
- **Exports and the server could be installed on a Python too new for them**: `requires-python` had no ceiling, so with
  Python 3.15 installed `uv` picked it and the install failed (a dependency has no 3.15 wheel). It now stops at 3.14.
- **Share-link frame allow-lists never worked behind nginx** (the production web image): `auth_request` does not expand
  variables, so the API was never asked which sites may frame a link's chat page and every page got an empty
  `frame-ancestors`. The token now travels in the request URI.
- **Two steps starting at once could get a model bridge that was not listening yet** (client base URL `http://127.0.0.1:0`): the
  OpenAI Agents SDK (and every framework that talks to the bridge) failed with "'tuple' object has no attribute 'choices'"
  on some runs of an experiment. Callers now wait for the bridge's port.
- Fixable high and critical vulnerabilities in the API image (certifi, filelock, pypdf upgraded; pip, whose vendored
  libraries were the rest, removed from the runtime image). Advisories published since (authlib 1.8.0, a critical
  signature bypass, and dnspython 2.8.0, both reached only through the optional MCP extra) are fixed by locking authlib
  1.9.1 and dnspython 2.9.0.

### Added
- Costs and budgets count GPT-5.6 Sol, Terra and Luna (with their price above 272K prompt tokens) and Gemini 3.8 Flash, at the providers' published prices (Gemini at its price from 2027-01-01; it is half that until then).
- A staging or production server now warns at startup when AGENTCANVAS_KMS_KEY is empty, and the deployment guide
  explains how to change AGENTCANVAS_SECRET_KEY without losing project secrets.
- GAPS.md of an export lists the workflow budget limits it does not enforce, and the editor warns about them (W026).
- A LangGraph export's service stops a run at the workflow budget's token, cost and time limits per run, as AgentCanvas
  does.
- Workflows on GPT-5 mini or Gemini 2.5 Pro/Flash warn before the provider stops serving them; GPT-5.6 Sol, Terra, Luna
  and Gemini 3.8 Flash appear in the model list.
- DeepSeek and xAI models default to deepseek-v4-pro and grok-4.7; workflows still on deepseek-chat or deepseek-reasoner
  get a warning that DeepSeek no longer serves them.
- Costs and budgets now count Claude Fable 5, Opus 5, Opus 4.8, 4.7 and 4.6, Sonnet 5 and Sonnet 4.6, Claude Haiku 5.5
  prompts above 100K tokens at their long-context price, and Sonnet 5.5 cache reads at $0.20.
- Press ? on any page (outside a field) to open the keyboard shortcuts.
- A shared chat whose last message never finishes answering stops checking after three minutes and offers Check again or
  Stop waiting, instead of staying locked.
- While the canvas is coloured by a version comparison it shows "Comparing with vX · Clear", even after the Versions tab
  is closed, and opening another workflow drops the comparison.
- While a test run in a tab waits for an approval or an answer, the tab's title starts with "(waiting)".
- In the test-run chat, the message you send appears straight away instead of after the answer.
- The Helm chart adds PodDisruptionBudgets (`podDisruptionBudget.*`) and spreads the API, web, sandbox and egress pods
  across nodes (`topologySpread.*`).
- The Helm chart can sign in through Matura (`config.authMode: matura`, `config.matura.*`) and refuses `scriptExecution:
  remote` with the sandbox service turned off.
- Red teaming has a multi-turn category for chat workflows: conversations that escalate over several messages and then ask for a secret phrase, built in or written by an attacker model.
- A caller with a deployment key can upload a file with `POST /v1/deployments/{id}/files` and send its reference in the run's input, instead of sending the bytes inline as base64.
- App connectors whose provider has neither client ID metadata documents nor dynamic registration can be used: an admin enters the client id and secret made at the provider under Settings › Connections.
- A schedule or webhook now warns when the workflow has an app connector that signs in as the person who started the run: a trigger's runs have no person, so the connector needs the service connection instead.
- Settings › Spending has a table by deployment, so what the runs of each published workflow cost can be told apart.
- Scanned PDFs and pictures can be added to a knowledge base: pages without text and PNG, JPEG, TIFF, BMP and WebP
  files are read with Tesseract OCR, which the Docker image now includes (`AGENTCANVAS_OCR`, `_OCR_LANGUAGES`,
  `_OCR_MAX_PAGES`; build-time languages with `OCR_LANGS`).
- The editor's top bar shows avatars of the other people who have the workflow open, with a badge on the block each
  one is selecting, and a notice when someone saves a newer version than yours. It works across several API servers
  and does not change how saving or conflicts work. (HARDENING G-4).
- The Evaluation page has a Fine-tuning tab that exports well-rated conversations (reviewed in annotation queues or
  given a thumbs up) or a dataset as JSONL, in OpenAI's chat fine-tuning format or plain messages, with tool calls
  and results included. A preview shows what is left out and why. (HARDENING G-10).
- Experiments have a red-team mode that makes adversarial inputs (prompt injection, jailbreaks, PII extraction, off-
  topic) from a built-in list or a model, runs them through the workflow and scores whether a safety block, a
  refusal or the agent held. It needs no model key to try. (HARDENING G-10).
- The Runs page has a search box that finds conversations by what was asked and answered, ranked with the project's
  embedding model (the built-in offline one by default). Runs are indexed after they finish, follow retention and
  stay inside the organisation; older runs can be indexed with one click. (HARDENING G-10).
- The Runs page and the editor's Timeline have a Trajectory view that shows a whole conversation as a tree: each
  run's steps, the helper agents a Planning Agent handed tasks to, model replies, tool calls, framework parts and
  frames, with time and tokens. Runs now keep their events, so it works for past runs too. (HARDENING G-10).
- App connectors that need a sign-in, such as Linear and Atlassian, now have a Connect button: each person signs in
  with their own account, and a run uses the connection of whoever started it, or the organisation's service
  connection when the block is set to that (for schedules, webhooks and deployments). Hosted app connectors no
  longer fail on a redirect option the MCP client sends. (HARDENING G-7).
- An admin can make a SCIM token under Settings > Organisation so Okta, Entra ID and other providers add, suspend
  and remove members and push groups that give roles. Everything is limited to the token's own organisation and
  audited. (HARDENING G-13).
- An admin can map the groups in the OIDC sign-in token to roles under Settings > Organisation. The role is set at
  every sign-in, people in a mapped group join without an invitation, and the last admin can never be removed by a
  token. (HARDENING G-13).
- People can make their own API tokens (act_...) in Settings, shown once, with a role at most their own and an
  optional expiry; the API accepts them as Authorization: Bearer next to sign-in, in their organisation only, and
  admins can revoke anyone's. Every change made with a token is audited. (HARDENING G-13).
- A LangGraph export now carries file and image fields (the service takes a file inline or by address, and the PDF
  and Word readers are added to its dependencies). Exports to other frameworks list file fields in GAPS.md as not
  shown to a model, and the editor warns (P008) when a workflow on another framework has them. (HARDENING G-5).
- The share page now shows a visitor's conversation again after a reload (kept on the server under their browser's
  id, until New conversation or the retention time). The chat widget takes postMessage commands (open, close,
  prefill) and reports ready and reply events, with the link's allowed sites checked. (HARDENING G-15).
- Ask AI and AI Agent steps now show the workflow's file and image fields to their model: images and PDFs as they
  are to a model that can see them, a PDF's text to one that cannot, plus a warning (W025) when a file goes to a
  model without vision. A step's Files to show the AI setting picks which fields it gets. (HARDENING G-5).
- In the Run tab and on a share page, a workflow input typed file or image gets an Attach box (drag and drop or a
  picker) that previews images and refuses files that are too large or the wrong kind. Visitors of a share link can
  send a file with or without a message. (HARDENING G-5).
- A workflow field can now be typed file or image; a person or an API caller uploads a file (up to 10 MB: images,
  PDF, Word, text) and runs carry only a reference to it, never the bytes. Files are kept as long as conversations.
  (HARDENING G-5).
- On a phone, the Runs page shows each conversation as a card instead of a table that runs off the screen. The
  Inbox, a share page and Runs are checked at 390 px wide. (HARDENING section 5).
- Tables, form fields and status badges share one look and behaviour across the Runs page, the workflow list and the
  Knowledge settings; column headers are always present and errors are announced. (HARDENING section 5).
- The Runs page can show only the conversations that started in the last 24 hours, 7 days or 30 days. The
  conversations API takes since and until. (HARDENING section 5).
- The editor opens in the Simple or Full mode you last chose on any browser you are signed in to. You can also set
  it under Settings › Appearance. (HARDENING section 5).
- Admins can set their organisation's own rate limits, switch local MCP servers off, keep the audit log longer and
  choose how long long-term memories are kept, in Settings › Organisation. The server's values stay the default and
  the ceiling. (HARDENING section 5).
- Settings has a Knowledge section where a project sets how new knowledge bases split their documents. Existing
  knowledge bases keep their own settings. (HARDENING section 5).
- On the Runs page, tick conversations and press Add these runs as examples to put them in an evaluation dataset.
  The one-purpose models/resolve API call is gone; GET model-defaults already answers for every purpose. (HARDENING
  section 5).
- Workflows no longer carry a Settings experimental flag that did nothing. Saved workflows open as before; the IR
  version is now 1.3. (HARDENING section 5).
- The Workflow panel has Defaults for every block (retries, timeout, cache) and Hide these fields from traces, which
  were only reachable through the API before. (HARDENING section 5).
- The Playground lists the project's workspace tools; tick the ones the model may call and each call and its result
  show above the answer. Making a workflow from the chat adds them as Python tool blocks. (HARDENING section 5).
- The Playground has a Temperature slider; it is switched off for models that always use their own sampling (such as
  current Claude models), and a workflow made from the Playground keeps the temperature only when its model takes
  one. (HARDENING section 5).
- When a published workflow has more than one text input, the share box has a "Messages go to" choice; leaving it on
  "Picked from the workflow's inputs" keeps the automatic pick. (HARDENING section 5).
- A knowledge base can be renamed and given a description from the Knowledge page (Rename…). The form warns that
  Search knowledge blocks naming the old name need the new one. (HARDENING section 5).
- AI model blocks get an optional "Use the cheapest of" list; each run picks the cheapest listed model that has a
  key here and that your organisation's policies allow, and the run records the choice. (HARDENING G-8).
- Admins get Settings › Organisation › Spending, with cost, tokens and cache hits per project, workflow and person
  for a date range (also GET /v1/org/spending). (HARDENING G-8).
- A model block's "Reuse answers for" cache now lives in the database, so all replicas share it and a restart no
  longer empties it. (HARDENING G-8).
- A workflow can be connected to a GitHub branch: each publish (or saved version, or on demand) opens or updates a
  pull request with the export, the workflow diff in the description and a CI check that runs the export's tests,
  and Import from branch brings the merged workflow back into the draft, refusing to overwrite changes made on
  either side. (HARDENING G-14).
- An AI agent can be designed for Claude Managed Agents and exported as the files the ant CLI applies: the agent,
  its environment, skills and MCP servers, with a session runner, make doctor and tests. (HARDENING G-12).
- Long-term memory can be searched by meaning: set the memory embedding model on the server and Recall returns the
  best matches directly from the database instead of scanning up to 1000 memories. (HARDENING F-7).
- A knowledge base can add a sitemap and fetch its web pages again every few hours or on demand, so changed pages
  replace their old text, new pages are added and removed ones are dropped. (HARDENING G-6).
- On Postgres, knowledge bases are searched inside the database (pgvector nearest-neighbour index plus full-text
  search) instead of loading every chunk, so a base can hold a million chunks; SQLite still searches in memory up to
  50,000. (HARDENING G-6).
- The Copilot panel's Stop button now asks the server to end the answer (POST /v1/workflows/{id}/copilot/stop)
  instead of only closing the browser's connection. The model call is cancelled at once, the part already said is
  saved as stopped, and viewers can stop their own Ask answers. (HARDENING G-11).
- `make copilot-evals` runs the Copilot's build and debug evaluations with a model you choose (Ollama by default)
  and writes one JSON report. With no reachable model it says so and runs an offline demo Copilot, so the command
  always finishes. (HARDENING G-11).
- Ask the Copilot to build or move a workflow for CrewAI, the OpenAI Agents SDK, Google ADK, Pydantic AI, Agno, the
  Microsoft Agent Framework, Strands, LlamaIndex or Mastra and it first reads which blocks that framework runs, only
  emulates or refuses. That list is generated from the same framework descriptions the validator uses, so it cannot
  go out of date. (HARDENING G-11).
- The Copilot can now list the project's secret names (never values), its knowledge bases with document counts,
  embedding model and whether one needs reindexing, and what an export of the workflow would hold and lack. It uses
  them in both modes to explain and point you to the right page; it still never adds secrets or exports by itself.
  (HARDENING G-11).
- **Import OpenAI Agents SDK code**, which is what OpenAI's Agent Builder exports before it closes on 30 November
  2026: **Import a file…** takes a `.py` file and reads it without running it. Agents become AI agent steps with
  their instructions, model and answer shape, in the order the code runs them; handoffs make a Planning Agent with
  specialists; `@function_tool` functions become Python tools whose code is added to the workspace; hosted tools,
  guardrails and branching are listed with what to use instead. The guide's page for switching covers Agent Builder
  and Flowise (archived in August 2026) (HARDENING G-1, P-4, P-5).

- **Published workflows answer A2A 1.0 clients**: `SendMessage` and `GetTask` with 1.0's wire form (`TASK_STATE_*`
  states, `ROLE_AGENT`, parts without `kind`, `{"task": …}`), the `A2A-Version` header (none means 0.3, as the spec
  says), and an agent card in 1.0's shape for a client that asks for it (`supportedInterfaces`,
  `httpAuthSecurityScheme`). 0.3 clients are served as before (HARDENING G-2, P-2).
- **Published workflows speak MCP 2026-07-28**, the current revision: no `initialize` handshake (the version and
  client travel in each request's `_meta`), `server/discover`, every result marked `complete`, tool lists that say
  how long they stay fresh (`ttlMs`, `cacheScope`), and the mirrored `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-
  Name` headers checked against the body. Clients that open with `initialize` keep the earlier revisions (HARDENING
  G-2, P-1).
- **Nine new templates, six of them advanced, and every feature has one**: Support inbox from email events (an
  event handler, prompt-injection and personal-data checks, workspace code, a question for a person, an approval),
  Lead scoring with research (a custom block, Python tools, tool add-ons, evaluation), Daily metrics report (a
  script, a schedule, an output-format check), Resilient research agent (backup models, retries, shorter chats, a
  to-do list, a quality check, scratch files, memory tools), Memory concierge (remember, recall, forget), Parallel
  code review panel (a group of steps, parallel branches, Wait for all), Content safety gate (harmful-content and
  output-format checks), Incident agent network (A2A and MCP agents, an agent from another framework, app
  connectors) and Editorial crew (a CrewAI crew and task). A test fails when a block in the palette or a feature
  outside the canvas (event handlers, scripts, custom blocks, Python tools, evaluation, saved tests, schedules) has
  no template. The hosted service no longer lists a template it cannot run (a block that runs on the host, E041).
- **W017: "Retry AI calls" connected after "Backup models" hides them.** The first add-on connected wraps the ones
  after it, and a retry that carries on when its retries run out (the default) answers the failure itself, so the
  backup models were never tried, without a word. The editor now warns and says how to fix it.
- **Home lists the project's workflows as a compact table, five a page**: name, version and last change per row,
  with search, a "Changed" filter (last 7 days, last 30 days, older) and sorting, and pages below; it loads every
  workflow, not only the first 200.
- **The Copilot writes workspace code as part of a proposal**: `list_workspace_files`, `read_workspace_file` and
  `propose_file` (checked with ruff, which it is told to fix). The files show in the proposal with a tick box and
  their code; accepted ones are written as one commit each, "Copilot: … (path, accepted by …)". Nothing is written
  before that, and Ask mode cannot propose files.
- **A conversation can switch the Copilot's model**: the model's name at the top of the panel opens a list; the
  choice holds for that chat (within the project's model policies and privacy setting) and New chat goes back to
  the project's. `PUT /workflows/{id}/copilot/model`; the conversation reports `chosen_model`.
- **The user guide inside the app** (`/guide`, from the person menu): the pages the Copilot searches, with tables,
  section anchors and links between pages; `GET /guide` and `GET /guide/{page}` serve them to anyone signed in.
- **Copilot answers cite the guide**: an answer that searched it ends with "From: Guide › Models › Local models"
  links to the sections it read (`sources` on the message).
- **What each Copilot answer cost**: its tokens (and dollars, where the model has a price) under it, kept with the
  conversation, and the conversation's total after a reload (`cost_usd` on the conversation and each answer).
- **Versions credit the Copilot** (FR-COP-07): a saved version lists the Copilot proposals accepted since the one
  before, "✦ Copilot (accepted by ada@example.com): Add a greeter · 4 changes". It is kept on the version
  (`copilot_changes` in `VersionOut`), so it outlives the conversation.
- **Evaluate is on the editor's bar**, and Simple mode keeps Versions (Publish), Evaluate and the More menu's
  Workspace, Knowledge and Memory: it hides advanced blocks and canvas tools, not the product.
- **The event log pages back and filters**: Events shows the newest 50, then "Load older"; a type filter offers the
  map's types and any logged. `GET /projects/{id}/events` takes `before` (an event id), `type` and `status`.
- **One top bar on every page** (`app/AppShell.tsx`): the project (switch, or create a new one), Workflows, Playground,
  Inbox with its count, Knowledge, Memory, Workspace, Events, Settings, and a person menu (who, role, organisation,
  theme, sign out). Moving between pages no longer goes through Home. The editor keeps its own bar and gets the same
  person menu.
- **Settings** at `/settings`, with a rail by scope: Project (Models, Copilot, Secrets, Observability), Organisation
  (Members, Policies, Audit log for admins) and You (Appearance). `/setup`, `/members`, `/audit` and `/policies` lead
  there. Secrets moved out of the Workspace sidebar into a table with a confirmation before delete.
- **The app's own dialogs and notices** (`ui/dialog.tsx`): every `window.confirm`/`window.prompt` (17 places) is a
  dialog with a title, an explanation and a named button (red for destructive ones), focus kept inside, Escape to
  cancel; `toast()` for short success notices.
- Projects can be renamed (`PATCH /projects/{id}`) and deleted with everything in them (`DELETE /projects/{id}`,
  which also removes the workspace folder and the cached sandbox); admins only, and an organisation keeps its last
  project. Settings › Project › General does both; deleting asks for the project's name.
- **Sandbox egress is enforced.** A project's `sandbox.json` host list was honoured only by LangSmith. Now the
  `docker` and `remote` modes give code exactly those hosts through an egress proxy (`agentcanvas.sandbox.egress`):
  each call's list is signed by the API, private addresses stay out of reach, and code has no other route out. The
  compose files and the Helm chart run the proxy (set `AGENTCANVAS_EGRESS_SECRET`); the `docker` mode starts its
  own. Modes that cannot limit the network refuse a list in staging and production. Settings › Sandbox edits the
  limits and the list, which it offers only where it is enforced.
- **Default models by purpose** (Settings › Models › By purpose): judge evaluators, simulated users and knowledge
  embeddings saved without a model get the project's default for that purpose, else the project's model, else a
  422 that says what to choose (`GET/PUT /projects/{id}/model-defaults`, `GET /projects/{id}/models/resolve`). The
  Evaluation page no longer pre-fills `anthropic:claude-haiku-4-5`.
- **Settings › Organisation**: General (the organisation's name and conversation retention, which had an API and no
  page) and Server (admins: how the server is set up, each setting with its environment variable; never a secret).
- The user guide has a page for the top bar and Settings, and its references to the old **Model**, **Members**,
  **Audit log** links and the Workspace's secrets are updated.
- **The Copilot on Deep Agents** (`AGENTCANVAS_COPILOT_ENGINE=deepagents`; the loop stays the default until the
  real-model evaluation): LangChain's agent harness runs the same tools, instructions and proposals, and adds text
  streamed as it is generated (`text_delta` events), the agent's plan as a live to-do list (`todos`), the whole
  conversation (summarised when long) instead of the last 24 messages, the full workflow as a file it reads instead
  of a copy cut at 30,000 characters, a sub-agent for side questions, and prompt caching for Anthropic models. It has
  no shell. Every Copilot test runs on both engines.
- **Review a Copilot proposal change by change** (FR-COP-01): each change in words with a tick box; unticking a
  step unticks the wires to it, ticking a wire ticks what it connects; the canvas previews only what is ticked, and
  blocks the proposal removes are drawn red and dashed instead of vanishing; tests and notes can be left out too.
  The decision records which changes were kept.
- **Copilot history**: New chat keeps the conversation (it used to delete it); the panel's History lists earlier
  conversations, titled by their first question, to reopen or delete (`/workflows/{id}/copilot/threads`).
- The inspector asks the Copilot about the selected block (and, for a builder, to fix its errors); `get_problems`
  gives the Copilot best-practice suggestions (a router without a default, a model without a backup, thin
  instructions) apart from the validator's problems (FR-COP-04).
- **The workflow list** on Home: search, sort by last change or name, and per workflow Rename, Duplicate (the
  draft and its layout; `POST /workflows/{id}/duplicate`) and Delete with a confirmation (`PATCH /workflows/{id}`
  renames). Dates read "3 min ago", with the exact time on hover.
- **Runs** (top bar): every conversation with the project's workflows, newest first, with status, where it came
  from (editor or API), when, how long, how many runs and their cost, the last error; filter by status and
  workflow, open one in the editor's timeline, delete one, load more. `GET /threads` takes `project_id` and returns
  each thread's workflow name and run summary.
- **The Inbox** names the waiting block (the API returns `node_label`) instead of showing node and thread ids,
  says how long it has waited, links to the run, filters by workflow, and confirms a decision with a notice. Its
  response type is the generated one.
- **Evaluators are a form, not JSON**: each kind's settings are a form built from its schema
  (`GET /evaluator-kinds`, with a label and what it checks), and evaluators can be edited and deleted (with a
  confirmation) from the list, which shows each kind by name.
- **Dataset examples are fields, not JSON**: one field per workflow input and expected output (numbers, true/false,
  lists and objects are read as JSON, everything else as text), with "As JSON" for nested values; removing an
  example asks first.
- **Annotation queues**: the rubric is rows (name, a scale from yes/no to 1-10, what a good score means) instead
  of "helpful:5, correct:2"; runs are added from a list of the workflow's recent runs instead of typed ids; items
  show when and why they were added. `GET /threads` returns each thread's `last_run_id`.
- **Experiment variants as rows**: "change <block>'s <setting> to <value>", with the block's settings listed from
  its schema and its current value shown, instead of an overrides JSON box.
- **Audit log**: column headers, actions in words, a date range (`since`/`until` on `GET /org/audit`), an entry
  opens to its details, and Export CSV.
- Inbox cards say what is asked ("An agent wants to use a tool") instead of the raw kind; unlabelled blocks are named
  from their name ("Manager approval"). Page descriptions sit in the page header on every page.
- **Workspace files as a folder tree** (folders open and close; the open file's folder starts open), and the New
  file box no longer wraps its button.
- Honest labels: the API documentation no longer shows the plan's work-item ids ("(S17)", "(M7-T3)"; a test keeps
  it so); beta blocks are marked in the palette; "stub risky tools" reads "tools that change things are only
  recorded".
- **Copilot**: skills it reads when they apply (debugging, a person in the loop, answering from documents, tests,
  dependable model calls); an investigator and a researcher sub-agent with read-only tools; the conversation's cost
  in the panel; test runs that call real models need the person's yes once per conversation; the command palette
  opens the Copilot or sends it what was typed.
- **Playground** streams answers (`POST /playground/chat/stream`) with Stop and Markdown, and keeps the chat while
  the tab is open.
- Home leads with the workflow list; empty states say what goes there; policies are edited in place; the Search
  knowledge block picks its base from a list; Members, Events and Memory get headers, confirmations and role gating;
  Settings > You lists the keyboard shortcuts; CI caches uv downloads.
- **Environments**: an organisation can list where workflows are published (Settings > Organisation > General);
  with a list, publishing elsewhere is refused; without one, any name works and dev, staging and prod are offered.
  The editor's Publish picks the environment instead of always using prod (migration `a8c4e2f6b0d3`).
- The editor's dock has a **History** tab: this workflow's past runs; one click opens a run in the Timeline.
- Workspace files can be renamed and moved (`POST /projects/{id}/files-move`; never over another file, never out
  of the workspace), from a button next to Save.
- The colour theme follows the person between browsers (`PUT /me/preferences`, `/me` returns it; migration
  `b2d6f0a4c8e3`). Simple mode stays per browser.
- Page headers with a title, a sentence on what the page is for, and breadcrumbs on a workflow's pages; Lucide icons
  (pinned at 1.52.0).
- API-wide robustness tests: every operation of the OpenAPI document is called with hostile bodies, ids and headers
  (`test_api_robustness.py`), and every template is damaged at random places and read through every endpoint that reads
  a workflow, then run (`test_workflow_fuzz.py`; `ROBUSTNESS_ROUNDS` and `ROBUSTNESS_SEED` lengthen the soak).
- Keyboard: the port menu takes the focus when a port is opened with Enter or Space, Tab stays inside it, and Escape
  returns the focus to the port (e2e `keyboard.spec.ts`).
- A warning (`sandbox.egress_not_enforced`) when a project's `sandbox.json` lists network access that the sandbox mode in
  use cannot give (only the LangSmith mode can).
- Local CI: `make ci-full` now runs the API suite on a throwaway Postgres container (it ran on SQLite only), and has the
  `frameworks` and `export` stages the GitHub workflow had; Helm runs from its official image when it is not installed.
  Each export's environment is deleted after its tests so a laptop's disk survives.
- `E2E_API_PORT` moves the end-to-end API off port 8000 when another program holds it.
- Hostile query parameters on every GET route (`test_api_robustness.py`), and on routes reached with real ids
  (`test_query_params_real_data.py`), so paging, cursors and version filters meet the values.
- `e2e/chaos.spec.ts`: every page against an API that answers 500, a proxy's 502 page, HTML with a 200, a dropped
  connection, `null` or an unexpected object: no page goes blank or throws an uncaught error, and none but the last
  (a different version) ends in the crash notice.
- `test_cross_org_every_route.py`: fills one organisation with one of everything, crawls the API as that organisation to
  find a working URL for 54 of the 61 routes that take an id, and sends each of them (and every other method the same
  path accepts) as a user of another organisation, who must be told 403 or 404. Found nothing; with scoping removed for
  two kinds of id it fails at once. Public by design and left out: share links and the A2A agent card.
- `test_secret_in_no_response.py`: a stored secret (also in a run's state and a model resource) is looked for in every
  response of every GET route it can reach, and in every file of every export zip. It is in none (a planted copy in a
  workflow name is found at once).
- Sign-in tests for tokens a stranger might send: alg `none`, HS256 signed with the issuer's public key, no expiry, not
  yet valid, issued in the future, wrong or missing audience, no or blank subject, a 200 kB token, malformed parts; and
  one that keeps the server from fetching the issuer's keys once per request when the key id is made up (60 tokens, at
  most two fetches; PyJWT's refresh cooldown, which this test now guards).
- `test_importers_fuzz.py` (Langflow, Flowise, n8n files damaged at random places must import into a valid workflow or be
  refused with the importer's own message; 1,500 rounds on three seeds found nothing) and `test_run_hostile_input.py`
  (runs with null, huge, deeply nested, NUL and lone-surrogate input end in a readable terminal event, leave readable
  records and free the thread; on SQLite and Postgres).
- `test_export_hostile_text.py`: workflow names, descriptions and step labels full of quotes, line breaks, shell and template
  syntax, paths and Unicode go into every deploy target and every framework export; each file must still parse and the
  text must stay text (never a key, an instruction or a path).
- Rendered Helm manifests (the platform chart, and every exported chart with autoscaling and ingress on) are checked
  against the Kubernetes 1.31 API schemas with kubeconform, in `make ci-full` and in GitHub CI. `helm lint` and
  `helm template` accept a field of the wrong type or name.
- A banner for failures nobody handled (a rejected promise from a fire-and-forget call used to vanish silently).
- `make ci-full`'s `export` stage deploys two exports with Docker Compose and smoke-tests them.
- `scripts/ci-images.sh` (the `images` stage of `make ci-full`): builds both images, scans them with Trivy, starts the
  production and the local compose stacks in projects and ports of their own and probes them through nginx (CSP, sign-in,
  sandbox isolation, database role, metrics, share-link framing).
- Tests that run over every route of the OpenAPI document: a stranger gets nothing from any route; a viewer or reviewer
  can do only what `ROLE_EXCEPTIONS` allows. Concurrency tests: 25 simultaneous runs, racing starts on one thread,
  racing decisions on one approval.
- The export plan lists the knowledge bases a workflow searches and whether the project has them; the export dialog says
  when one is left out (the exported search would fail).
- `make lint` (and the pre-push hook and the GitHub workflow) run **oxlint** on the web app: hooks called conditionally
  are errors, effects with missing dependencies are warnings that must carry a reason to stay. (typescript-eslint does not
  support TypeScript 7 yet, so ESLint could not be used.)
- Local CI stage `mastra-types`: the Mastra runtime (TypeScript that Node runs with its types stripped) is checked with
  `tsc --strict`.
- An e2e check that every control in the inspector has an accessible name and stays in the tab order.

### Changed
- The support triage example starts on GPT-5.6 Luna instead of the retiring GPT-5 mini.
- A temperature set on any Claude model (including Haiku 4.5, Sonnet 4.5 and ids the catalog does not list) is no longer
  sent, so those runs stop failing with a 400; W020 says so in the editor.
- The "API down" alert no longer fires forever with the standard kubernetes-pods scrape job, and every alert links to
  runbooks that now exist.
- Compose backups now include the workspaces volume, a restore onto a new Postgres server re-creates the API's database
  role first, and the deploy guide lists the keys to keep with each backup.
- `make infra` publishes Postgres and Keycloak on 127.0.0.1 only and no longer starts Redis, which the API never used.
- The production compose file binds the web port to 127.0.0.1 by default (`WEB_BIND`), runs the API with a read-only
  root, no capabilities and a memory limit, and pins the Postgres image by digest; the deploy guide explains Postgres
  major upgrades.
- The production compose file starts Postgres with `max_connections=200` (`POSTGRES_MAX_CONNECTIONS`), and the deploy
  guide gives the formula for sizing it.
- With Docker Compose, every setting in `infra/.env.prod` (or `infra/.env.local`) now reaches the API, not only a fixed
  list; `AGENTCANVAS_EGRESS_ALLOW_PRIVATE` reaches the egress proxy.
- The Helm chart no longer assumes published images (set `image.api.repository` and `image.web.repository`), and `helm
  install`/`upgrade` fail when the Secret lacks `LANGGRAPH_AES_KEY`, `AGENTCANVAS_SANDBOX_TOKEN` or
  `AGENTCANVAS_EGRESS_SECRET`; the deploy guide lists every key.
- The server uses the Anthropic SDK 2.3 and LangChain's Anthropic package 1.7.7, the current major versions.
- No change for users: the end-to-end tests for the editor, debugging and Google ADK now pass with a real model as
  well as the demo one. (HARDENING section 5).
- Faster Copilot and evaluation: the Copilot keeps the knowledge pack of each project shape (two projects with their
  own blocks rebuilt it on alternate requests), the guide search computes its statistics once, a composite evaluator
  scores its parts side by side, and adding a document to a knowledge base embeds it before locking the base, so
  other adds no longer wait behind the embedding provider (HARDENING F-5).
- The Runs list and a conversation's runs no longer load each run's recording (every model reply and tool output) to
  sum its tokens, and the Inbox reads each workflow once rather than once per waiting item (HARDENING F-2).
- Masking secrets in a run's events no longer copies every event through JSON: an event without a secret (nearly
  all, every streamed token among them) is checked in one pass, about twice as fast (HARDENING F-1).
- Exports and framework environments now install openai-agents 0.24.0, CrewAI 1.15.33, Google ADK 2.13.0 (with
  LiteLLM 1.105.7; 1.106 needs the mcp package it does not declare), Pydantic AI 2.63.0, Agno 3.1.11, Strands
  1.62.0, Agent Framework 1.23.0 / 1.17.0, Claude Agent SDK 0.2.174, and @mastra/core 1.80.0 with @ai-sdk/openai-
  compatible 3.0.80 and zod 4.6.7. Run `make framework FW=<id>` (and `make mastra`) again to update a local
  environment.
- LlamaIndex exports and the LlamaIndex environment installed whatever llama-index-workflows was newest, and a
  workflow named like a framework (e.g. "CrewAI") got a package of that name in its export, hiding the framework,
  when the server lacked it. Exports now pin the tested llama-index-workflows, such names get a `wf_` prefix, `make
  crewai` uses Python 3.13 (CrewAI does not support 3.14), and a test checks every framework pin against the one
  list. (HARDENING X-8).

- **The model catalog lists the current models, with prices**: Claude Haiku 5.5 ($0.10/$0.50 per million tokens, 1M
  context) and Fable 5.1, the GPT-6 family (Astra, 6.1 Sol, 6.1 Terra, Luna) and Gemini 3.6 Flash; GPT-5.5 and GPT-5
  mini now have prices, so costs and USD budgets count OpenAI models too. Templates use Claude Haiku 5.5 as their
  quick model (Haiku 4.5 cost ten times as much). A model its provider has announced it will retire shows the date:
  it is not offered for new choices, and the editor warns where a workflow still uses it (W023; Claude Sonnet 4.5
  retires on 2026-11-30) (HARDENING E-9, P-3).
- Saving the project's model from Settings stays in Settings with a notice; only the first choice, from the Home
  banner, goes back to Home.
- Home, Members, Copilot and Editor pages read the session through `useMe`/`useCan`, so they redraw on a role change.
- `make ci` runs the gates locally (check, strict docs build, dependency audit, Helm lint); `make ci-full` adds the
  framework environments, the whole API suite, the Docker images and e2e. `make hooks` runs lint, types and web tests
  before every `git push`. The GitHub workflow now runs only when started by hand.
- `HARDENING.md` keeps the hardening plan: what is done (with versions) and what is open.
- Finished runs' events are also evicted by size (256 MB), not only by count.
- `decisions.md` records the judgment calls made while hardening without asking.
- Servers clean up the temp folders of framework workers that a killed server left behind (the folder name now carries the
  server's process id; only folders of dead processes are removed).
- Nine hand-written response types in the web app are now the generated API types, so a change to the API fails the
  type check instead of going unnoticed.

### Upgrading

- Migration `d8f2b6c0e4a9` adds `copilot_conversation.model` (empty: the project's model).
- Migration `c6e0a4b8d2f7` adds `workflow_version.copilot_changes` (an empty list for existing versions); it runs with
  the others at start-up.
- Production compose needs `AGENTCANVAS_EGRESS_SECRET` (32+ random characters) in `infra/.env.prod`; a Helm install
  with `sandbox.egress.enabled` (the default) needs the key `AGENTCANVAS_EGRESS_SECRET` in its Secret.

## v0.32.3 — Session state in the web app, and a guard on framework versions

### Changed
- The signed-in user, organisation and role are now state (`useMe`, `useCan` in `api/session.ts`); components that use
  them redraw when they change. `ME` and `can` still work for code outside React.

### Added
- A test that the framework versions written in the export lists, `interop.py`, the CrewAI code generator and the CI
  matrix agree, so a version bump in one place fails until the others follow.

## v0.32.2 — The canvas can be used from the keyboard

### Added
- Palette items add on Enter or Space. Every port on a block is a focusable button: Tab to it, press Enter or Space and
  the "what can connect here?" menu opens next to it, so blocks can be wired without a mouse.
- Blocks announce their name and type to screen readers; a port's hover card also shows when the port has focus.

## v0.32.1 — The canvas no longer redraws on every streamed token

### Fixed
- While an agent streams its answer the canvas rebuilt every node for each token. The streamed text now reaches the
  canvas at most about eight times a second (and always ends on the full text); the Run panel still shows every token.

## v0.32.0 — Parallel branches run in parallel on the frameworks that use the shared flow engine

### Fixed
- **Strands workers failed to start** ("worker_env() got multiple values for keyword argument 'OTEL_SDK_DISABLED'"):
  the Strands adapter repeats a default the worker sets itself, and the v0.29.0 environment allowlist passed both.
  A worker's `extra_env` now overrides the defaults.

### Changed
- **Steps side by side now run at the same time** on Pydantic AI, Agno, the Microsoft Agent Framework, Strands,
  LlamaIndex, CrewAI and Google ADK flows, the OpenAI Agents SDK and Echo (the shared `supersteps` engine). They ran
  one after another, so a slow branch delayed the others and a later branch could see an earlier branch's update.
  Each step in a round now gets the values the round started with (as in LangGraph), and the round's updates merge
  afterwards in the order the canvas lists the steps, through each field's reducer.
- A step that asks a person no longer loses its siblings' work: those that finished are kept, another step that also
  asks runs again once the first is answered.

## v0.31.1 — The local Docker app uses the sandbox service too

### Changed
- `infra/compose.local.yml` (`make docker-up`) runs workspace code in the `sandbox` service instead of a child of the
  API: same image, none of the API's keys or database access, read-only root, no route out. Nothing to configure
  (`AGENTCANVAS_SANDBOX_TOKEN` has a local default). Run `make docker-up` again to pick it up.

## v0.31.0 — A real sandbox: workspace code runs in a service of its own

### Added
- **The sandbox service** (`script_execution=remote`, `agentcanvas.sandbox.service`). For each call the API sends the
  project's files (a tar.gz without git history), the function and its input; the service unpacks them into a fresh
  temporary directory, runs the sandbox child with the usual limits and no network, returns the result and deletes the
  directory. It has no database credentials, keys or other projects' files, refuses archives with absolute paths, `..`,
  links or too many bytes, and checks a bearer token (`AGENTCANVAS_SANDBOX_TOKEN`).
- **Production defaults to it.** `infra/compose.prod.yml` runs a `sandbox` service (read-only root, all capabilities
  dropped, process and memory limits, an `internal` network only the API shares) and the Helm chart a `sandbox`
  Deployment, Service and NetworkPolicy (ingress from the API only, no egress). Set `AGENTCANVAS_SANDBOX_TOKEN`
  (compose `.env.prod`, Helm secret key of the same name). The hosted edition accepts `remote`. CI checks the
  service runs as the app user, holds none of the API's secrets and has no route out.

### Changed
- `docker`, `subprocess`, `inprocess` and `langsmith` work as before; egress allow-lists in `sandbox.json` still need
  `langsmith` (the service has no network).

## v0.30.1 — Hardening: a production server says when workspace code is not isolated

### Changed
- In staging and production, an API running workspace code in-process or as a child process logs a
  `sandbox.not_isolated` warning at start-up. The compose default stays `subprocess`: `docker` mode needs the host's
  Docker socket (which is root-equivalent) and a Docker CLI the API image does not carry.

## v0.30.0 — Share links choose which sites may frame their chat page

### Added
- **Allowed sites for the chat widget.** A share link's chat page could be framed by any website. Builders can now
  list the sites (`https://example.com`, `https://*.example.com`) under **Share as an app or a chat widget**; nginx
  asks the API per link (`GET /v1/share/{token}/frame`, `auth_request`) and sets `frame-ancestors` to those sites.
  Nothing listed keeps today's behaviour (any site), so embedded widgets keep working until their owner narrows
  it; an unknown or switched-off link allows only the app itself. Migration `c9e1a7d3f5b2` adds
  `deployment_share.frame_origins`.

## v0.29.1 — Hardening: Echo pauses survive restarts, runs always use their own project's workspace

### Fixed
- **Echo keeps paused runs with their conversation** (`runtime/paused.py`), like every other framework, so a resume
  on another replica or after a restart works.
- **A run uses its own project's workspace even when the process-wide `PROJECT_OF` map has no entry** (a replica that
  never loaded the workflow, or a restart): `start()` reads the workflow's project from the database.

## v0.29.0 — The rest of the deep review: run correctness across replicas and server hardening (not tagged yet)

The second round of fixes from the 2026-10-18 review (v0.28.1 has the first).

### Fixed
- **Paused runs live with their conversation.** CrewAI, Google ADK, the flow-engine frameworks, Mastra and the
  OpenAI Agents SDK kept a paused run in the memory of the API process that paused it, so a resume that reached
  another replica, or came after a restart, failed with "nothing to resume". `thread.paused_state` (migration
  `b8d2f6a0c4e7`) keeps it; `runtime/paused.py` is the store. Several API replicas now work for every framework.
- **The OpenAI Agents SDK and Echo engines** had the superstep bugs `supersteps.py` fixed in v0.26.1: a join inside a
  loop fired early on its second pass, a join after a route not taken ended the run at the recursion limit, and a
  resumed round ran the next round's steps twice. Fixed in both, and so in OpenAI Agents SDK exports.
- **One run per conversation across replicas.** A run claims its thread in the database; a second replica's run on
  the same conversation gets 409.
- **Row-level security covers every session of a Matura request**, including those the runner, the Copilot, gates,
  the audit log and knowledge search open (`TenantSession`), not only the request's own.
- **Workspace saves** run git and ruff in a worker thread, one save at a time per workspace, instead of stalling
  every stream on the server.
- **A slow user regex** no longer fails the other regex searches it shared the pool with; they start again.
- **Framework workers**: an allowlisted environment (no database URL, keys or cloud credentials), a malformed or
  over-long message no longer ends every run on a worker, writes no longer block the event loop, a run whose worker
  is silent for 10 minutes ends with a timeout, and the Mastra worker restarts after 500 distinct flows (Node.js
  cannot unload modules).

### Added
- **P007** (warning): the workflow's framework does not apply node policies (retry, timeout, cache, error route).
  Every framework but LangGraph used to drop them without a word.
- **E999**: a validation check that fails is reported instead of turning every edit into a 500.
- A workflow written by a newer AgentCanvas gets a plain message; IR migrations have a registry
  (`ir.models.MIGRATIONS`).
- Request bodies over 20 MB get 413 from the API itself, not only behind nginx.

### Security
- With `subprocess` sandboxes, the API process is made non-dumpable on Linux, so a same-user script cannot read the
  server's memory or environment through `/proc`.

### Changed
- `compose.prod.yml` requires `LANGGRAPH_AES_KEY` (checkpoints encrypted at rest) and no longer asks for one API
  replica. CI sets it and `POSTGRES_APP_PASSWORD`, and checks that the API's database role is not a superuser.
- CI: read-only token permissions, one run per branch or PR (newer pushes cancel older runs), a time limit per job.

## v0.28.2 — Keys for local model servers (not tagged yet)

### Fixed
- **Local model servers that require a key can be set up.** The first-run screen offered an API key only for hosted
  providers and "Any OpenAI-compatible API", so LM Studio with authentication on, or vLLM and llama.cpp started with
  `--api-key`, could not be used. LM Studio, vLLM and llama.cpp now take an optional key, stored as the project
  secret `LMSTUDIO_API_KEY`, `VLLM_API_KEY` or `LLAMACPP_API_KEY` (`optional_key_env` in `providers.json`), and
  chat models of those providers use it when their config names no key (`nodes.OPTIONAL_KEYS`, the server variable
  of the same name works too); without one they keep the placeholder key.
- **Event handlers and custom nodes run in docker and LangSmith sandboxes.** The sandbox child runs on an image
  without AgentCanvas, so workspace code's `from agentcanvas.sdk import on` (or `canvas_node`, `Event`, `Actions`)
  failed with `ModuleNotFoundError` there; the tests passed only because the test machine's Python had AgentCanvas
  installed. The child now supplies a stand-in `agentcanvas.sdk` when the real one is missing (like its stand-in
  `@tool`); `emit` and `secret` say they need the host.

### Added
- **Find models on the server** on the first-run screen: `POST /v1/projects/{id}/setup/server-models` reads a local
  server's or OpenAI-compatible API's model list (`/api/tags` for Ollama, `/models` otherwise; the typed or saved key
  is sent) and the names fill the Model box's suggestions, so a model loaded in LM Studio can be picked instead of
  typed.

## v0.28.1 — Fixes from the deep review of 2026-10-18 (not tagged yet)

The critical and high-severity findings of a whole-codebase review that could be fixed without new design. The
review and the plan for the rest are in the "AgentCanvas — Deep Review and Improvement Plan" doc.

### Security
- **All workspace code stays in the sandbox.** With `AGENTCANVAS_SCRIPT_EXECUTION` other than `inprocess`, Python
  tools used to be imported and run in the API process (where they could read every project's decrypted secrets
  and the server's keys), and a Script step whose import path started with `tools.`, `nodes.` or `events.` skipped
  the sandbox. Now every tool call is one sandbox run (the tool's name, description and arguments are read from its
  source by `workspace.catalog.tool_spec`, so the module is never imported by the server), and Script steps from
  any workspace folder go to the sandbox. The sandbox child supplies a stand-in `@tool` where LangChain is missing.
- **Every run passes the security rules first** (`services/run_gate.py`, a runner prepare hook): E041, E042, E043
  and E080. Cron and webhook triggers without a deployment, event handlers' `start_workflow` and hand-over used to
  run the draft unvalidated, so a builder could have a local (stdio) MCP server's command run by a schedule.
- **The API connects as an ordinary database role.** Superusers skip row-level security, and the compose stacks
  connected as the Postgres image's superuser. `python -m agentcanvas.db.app_role` creates the API's role (no
  superuser, no `BYPASSRLS`); `compose.prod.yml` runs it after the migrations and connects the API as
  `agentcanvas_app` (new required `POSTGRES_APP_PASSWORD`); the Helm migrate Job does the same with
  `MIGRATE_DATABASE_URL`, `APP_DB_USER` and `APP_DB_PASSWORD` in the secret. In Matura mode the API refuses to
  start in staging and prod on a role that bypasses row-level security, and warns elsewhere.
- `compose.local.yml` (no sign-in) listens on 127.0.0.1 only; `WEB_BIND=0.0.0.0` opens it on purpose.

### Fixed
- **Pauses on CrewAI, Google ADK, the flow-engine frameworks and Mastra** were named `<conversation>:<block>`, so a
  second pause at the same block of a conversation (a loop through an approval, a re-asked question, the next chat
  message) reused a decided inbox item and could not be answered. Pause ids are now unique per run.
- **Hand-over** after a redeploy or a lost server restarted runs of frameworks without saved state from the
  beginning with no input, repeating their side effects. Such runs now end with an error that says to start again.
- **Web: queued edits are kept.** Opening another workflow saved nothing still waiting in the queue, and a 409
  conflict threw away the edits typed while a save was in flight. Queued edits are now saved, edits refused by a
  conflict are re-applied on the latest version (or reported when they no longer fit), and closing the tab with
  unsaved edits asks first. A failed layout save shows a notice.
- **Web: streams stop when nobody reads them.** Run and Copilot streams kept reading after the editor was left,
  and a stale run's events could land in a fresh run panel (A → Home → A). One malformed frame no longer ends a stream.

### Changed
- Versions follow the API: `pyproject.toml`, `package.json` and the Helm chart's `appVersion` (it said 0.2.0).
- `compose.prod.yml` says that the `subprocess` sandbox is limits only, and that flows on frameworks other than
  LangGraph need `API_REPLICAS=1` until paused runs move to the database.

## v0.28.0 — AgentCanvas on Matura: workspaces, database isolation, portal, audit sink (not tagged yet)

The rest of Matura's sprint TS26 (full adoption) that fits AgentCanvas today.

### Added
- **Matura workspaces group projects.** `project.workspace_id` (nullable, migration `a3c7e9b1d5f2`); `GET /v1/workspaces`
  lists the organisation's active workspaces from the contract tables; `GET /v1/projects?workspace_id=…` filters;
  `POST /v1/projects` takes `workspace_id`; `PUT /v1/projects/{id}/workspace` moves a project in or out. Matura mode
  only (422 otherwise, and for an archived workspace or another organisation's). The home page has a workspace picker
  that opens a project of the picked workspace (creating one for builders) and shows the project's name.
- **Row-level security on Postgres.** Forced policies (`agentcanvas_tenant`) on the 35 organisation-owned tables: an
  `org_id` is compared with `matura.tenant_id`, other tables follow their parent up to the project. In Matura mode
  every transaction of a signed-in request sets `matura.tenant_id` to the token's organisation, so a query missing its
  organisation filter returns nothing of another. API connections start with `agentcanvas.all_tenants=on`, so
  background work and the `dev`/`oidc` modes see every row as before; a connection that sets neither sees none.
  SQLite is unchanged. `db/rls.py` lists every table as covered or exempt (with the reason), and a test fails when a
  new table is neither.
- **Links to Matura's customer portal**: `AGENTCANVAS_MATURA_PUBLIC_URL` gives `<public URL>/portal/` (or
  `AGENTCANVAS_MATURA_PORTAL_URL`); `GET /v1/me` returns `portal_url`, the organisation's page there (`<portal>/t/<tenant id>/`), which
  Members (`…/members`) and Policies (organisation settings) link to.
- **Audit sink.** Security-relevant audit entries (publish, unpublish, key rotation, secrets, approvals, members,
  organisation settings and policies, release gates, trace export, project workspaces) also go to an `AuditSink`. The
  default does nothing; `AGENTCANVAS_MATURA_AUDIT_URL` sends each as a signed CloudEvent, a placeholder until Matura has
  an audit ingest API.

### Changed
- **Shared data mode only**, as `matura/manifest.json` declares: a token whose `pmode` is `schema` or `database` is
  refused with 503. Schema- or database-per-tenant placement is not supported (one engine and background jobs that
  work across organisations).
- `GET /v1/auth/config`'s `portal_url` ends with `/`.

## v0.27.0 — Sign-in through Matura (not tagged yet)

### Added
- **`AGENTCANVAS_AUTH_MODE=matura`**: sign-in, organisations, members and roles come from Matura, the owner's identity
  and tenancy platform (its sprint TS9a). The token names the organisation (`tid`) and what the person may do there
  (`perms`); the highest role whose permissions the token carries in full decides the role (a role edited in Matura
  that skips a rung gets only the rungs below it), so every existing role check works unchanged. People in several organisations switch with a picker that signs in again for the one they choose.
- **Matura's webhooks** (`POST /v1/matura/events`, HMAC-signed) and **its change feed** keep organisations, people and
  roles current: a role granted, changed or revoked in Matura, a member removed or AgentCanvas turned off for an
  organisation applies here at once, and whatever a missed webhook carried arrives at the next feed read.
- **Matura contract tables** (`matura_*`, migration `f2b6d0a4c8e1`) on every install; empty unless Matura is used.
- **`matura/manifest.json`**: what Matura needs to register AgentCanvas (four permissions, four starter roles).
  Its `conformance` section lets Matura's `matura-conformance` check AgentCanvas from outside; it passes all 19 checks
  (token refusals, permissions, organisation isolation on projects, signed webhooks).
- `GET /v1/me` also returns `org_names`.

### Changed
- In Matura mode the member and invitation endpoints answer 409 and the Members page is read-only with a link to
  Matura's portal. `dev` and `oidc` modes are unchanged.

## v0.26.3 — Every framework in the Docker app by default (not tagged yet)

### Changed
- **`make docker-up` builds every framework's runtime into the API image** (CrewAI, Google ADK, Pydantic AI, Agno, the Microsoft Agent Framework, Strands, LlamaIndex and Mastra), so their workflows run in the Docker app without extra steps. `docker compose -f infra/compose.local.yml up --build` does the same. The image is several GB; `FRAMEWORKS="crewai mastra"` builds only some, `FRAMEWORKS=none` none. An image built with v0.26.2 or earlier needs one `make docker-up` to rebuild. The production images (`compose.prod.yml`, Helm) are unchanged and still opt in.

## v0.26.2 — Framework setup, export to any framework, Full mode fits (not tagged yet)

### Fixed
- **"Runtime not available" when the app runs in Docker.** `make crewai` / `make framework FW=…` on your computer does not reach the server inside Docker. The API image can now include them: `make docker-up FRAMEWORKS=all` (or a comma list such as `FRAMEWORKS=crewai,google_adk,mastra`). The server says in Docker which command to run instead of the from-source one.
- **The Framework setting says when a framework is not set up** and what to run (`/frameworks` gives `ready` and `setup`); the top bar marks such badges "not set up". The list refreshes every 30 seconds, so a framework set up meanwhile shows as ready.
- **Export lists every framework.** *Download code* can package a workflow for any installed framework (`export.zip?framework=`), not only its own; the workflow itself is unchanged.
- **"Where it will run" no longer fails** on a workflow that is not LangGraph: the deploy kit is for LangGraph services, so it shows only for them, with a note for the others.
- **Full mode fits the window.** The top bar needed about 1,830 px and cut off its right side. Rarely used links and tools moved into a **More ▾** menu, the framework badges collapse into "+N", and the bar fits at 1,280 px.

## v0.26.1 — Review after the competitive roadmap (not tagged yet)

A whole-codebase review after S23–S36 found 40 bugs; all are fixed.

### Security
- **A workflow's name, description or block ids can no longer become code.** Generated Pydantic AI, Agno, Microsoft Agent Framework, Strands, LlamaIndex, OpenAI Agents SDK, CrewAI and Google ADK modules escape them in docstrings and comments, and Mastra's TypeScript escapes `*/` in its header. Before, a name such as `x"""; import os; ...` ran on the server when the flow loaded, and a name with `C:\Users` or a quote at the end could not run or export.
- **Mastra prompt templates are sandboxed** like Python's: `constructor`, `prototype` and `__proto__` are refused, so a template cannot run JavaScript in the worker.
- **Public share links:** a visitor no longer sees the raw tokens of internal steps or the text before a safety block redacts it (only the final step's text streams, and nothing streams when the workflow has a safety block); a visitor can answer questions but cannot approve an approval step (it waits in the inbox); each visitor address has its own rate limit (`rate_limit_per_share_client`, default 30 a minute), so one visitor cannot use up an API key's quota.
- **Knowledge uploads:** a Markdown heading with a long run of spaces no longer freezes the server for minutes (the heading pattern is linear); a Word file that unpacks to more than 100 MB is refused before it is read; parsing and chunking run off the event loop.
- **Imports:** an n8n, Langflow or Flowise file with many branches that join again no longer takes exponential time, and a very deep one gives a clear error instead of a 500.
- **Personal data patterns** are matched across the whole text, not only its first 100,000 characters.

### Fixed
- **The flow engine** (Pydantic AI, Agno, Microsoft Agent Framework, Strands, LlamaIndex and Mastra): a *Wait for all* step inside a loop waits for every branch on each pass instead of running early and twice; one after a path that was not taken ends the run instead of failing at the step limit; a step after an approval no longer runs twice when the run continues.
- **Tool approvals** never read a "no" as a "yes": `"reject"`, `{"approved": false}` and a missing decision all reject, and one edit is no longer copied onto other calls.
- **A failed continue keeps the paused run** (on the server and in exports), so the person can try again; a worker that stops mid-run ends the run with an error.
- **Microsoft Agent Framework:** each decision applies to its own call when a turn calls the same tool twice, and a rejection's reason reaches the model.
- **LlamaIndex:** every call of a turn that needs a person is asked about, not just the first; the model bridge streams one tool call per chunk, as OpenAI does, so LlamaIndex agents see parallel tool calls.
- **Model call limits** hold across approvals on the Microsoft Agent Framework and Strands.
- **Google ADK:** *Ask before using* works with the tool block's name (the one validation accepts); a call approved earlier is not run again when the run continues; a call whose arguments changed is asked about again. LangChain designed agents also accept the block name.
- **Exports:** a workflow named after a package it imports (`Strands`, `Agent Framework`, `workflows`) gets a `wf_` package name; the export download keeps its `.zip` name when the workflow searches a knowledge base.
- **Mastra:** an empty field prints `None` and booleans `True`/`False` as in Python, empty lists are false in `{% if %}`, `tojson` works; an answer schema with `$id` no longer breaks later answers; cancelling a run ends it even while a tool runs; protocol writes retry when stdout is busy.
- **Budgets** count the spend of runs still going, so runs at the same time cannot all spend up to the limit (per API instance).
- **Knowledge bases:** two uploads at once no longer leave search on a stale index, and a reindex no longer misses a document added meanwhile.
- Two first opens of a deployment's share settings at once no longer give a 500.
- **Web:** typing in the share settings is kept when the switch is flipped; a knowledge base name can be typed with spaces and dashes; *Alert at* takes at most 5 values; Clear in the Playground drops a reply still on its way; the Code tab's highlight stops before `FLOW`.

## v0.26.0 — Mastra flows in TypeScript (not tagged yet)

### Added
- **Mastra as a workflow framework, the first TypeScript one.** Pick it in the workflow settings and the workflow is generated as a TypeScript module: a Mastra `Agent` for each AI block (an AI SDK model over an OpenAI-compatible address, `createTool` tools, a Zod schema for typed answers through `structuredOutput`), an `async function` per block and a `FLOW` that says which step follows which (`@mastra/core` 1.76.0). It passes the 12-case conformance kit; 22 of the 27 templates run on it.
- **A TypeScript twin of the flow engine** (`adapters/mastra/flow.ts`) runs the flow as the canvas draws it, like `supersteps.py` does for the Python frameworks: steps side by side, Wait for all, paths by CEL rules (`@marcbachmann/cel-js`, with whole numbers read as CEL ints as in cel-python), loops stopped at the step limit, field reducers, prompts with `{{ state.<field> }}` (Nunjucks; lists and objects are written as JSON), answers checked against a schema (Ajv). Node.js 22.18+ runs the TypeScript as it is, with no build step.
- **A Node.js worker** (`adapters/mastra/worker.ts`) runs Mastra flows next to the server and speaks the same JSON lines as the Python frameworks' workers, so models, tools, knowledge bases, workspace code, usage and recorded tool calls all stay on the server. `make mastra` installs Mastra and the runtime's packages from npm into `apps/api/.frameworks/mastra`.
- **Ask before a tool call, the Mastra way.** Tools named in *Ask before using* get `requireApproval`: the agent's run suspends, its snapshot is saved with the pause (so it continues in another process too), and the agent continues with `approveToolCallGenerate` or `declineToolCallGenerate` (the person's reason reaches the model). Edited arguments are applied when the approved call runs. Two calls that need a person are asked about one after the other.
- **Model call limits** use Mastra's `maxSteps`: past the limit the agent stops with a note, or the run fails when the block says so.
- **Export to a TypeScript project**: `package.json` with pinned packages, `src/workflow.ts`, the vendored `flow.ts` and `runtime.ts`, a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner (`node src/main.ts`) that saves a paused run in `.runs/` and continues it, and `node --test` tests that replay saved test cases with a scripted model. Workspace code stays Python: tools, code steps and knowledge-base search run in a small Python helper (`python/sidecar.py`) when the flow has them. CI exports every template Mastra can run and runs each project's tests.
- Servers offer Mastra by default (`AGENTCANVAS_ADAPTERS=...,llamaindex,mastra`).

### Changed
- `CrewWorker.prepare()` lays out a worker's files and names its command, so a worker can be something other than Python.

### Fixed
- The demo model picks the first choice for a tool argument that has a list of choices, instead of "demo". Typed answers on Strands and LlamaIndex (which ask for them through a tool) are valid again with the demo model, so the Lead qualification template runs on both.

### Known limits
- The flow is not written as Mastra's own `createWorkflow` chain (the roadmap's "Full" tier, if users ask). The API image has no Node.js: Mastra workflows run there only once the image is extended with Node.js and `make mastra`.

## v0.25.0 — Strands and LlamaIndex flows (not tagged yet)

### Added
- **Strands Agents and LlamaIndex as workflow frameworks.** Pick either in the workflow settings and the workflow is generated as a flow of async Python on the shared flow engine. Strands agents are `Agent`s with an `OpenAIModel`, tools and a `structured_output_model` (`strands-agents` 1.58.1); LlamaIndex agents are `FunctionAgent`s with an `OpenAILike` LLM, tools and an `output_cls` (`llama-index-core` 0.14.25). Both pass the 12-case conformance kit; 22 of the 27 templates run on each.
- **Ask before a tool call, each framework's way.** On Strands, an `AskBefore` hook interrupts the call with `BeforeToolCallEvent.interrupt`; the agent's messages and interrupt state are saved and it continues with an `interruptResponse` (approve, edited arguments, or a rejection whose reason the agent reads). On LlamaIndex, the tool waits inside the agent's workflow with an `InputRequiredEvent` and a `HumanResponseEvent`; the serialized `Context` and the chat memory are saved, and calls are asked about one at a time.
- **Model call limits** use Strands' `Limits(turns=...)` and LlamaIndex's `max_iterations`.
- **Exports** to a Strands or LlamaIndex project, like the other flow frameworks; their tests script the model with an OpenAI-compatible endpoint in memory that streams. CI exports every template each can run and runs each project's tests.
- Servers offer both by default; `make framework FW=strands` / `FW=llamaindex`, and the API image takes `--build-arg WITH_STRANDS=true` and `WITH_LLAMAINDEX=true`.

### Changed
- `flowgen.Dialect` names the class an agent builder returns (`FunctionAgent` for LlamaIndex).

### Fixed
- The demo model no longer fails with "unhashable type: 'list'" on a tool argument whose type is a list such as `["string", "null"]` (Strands sends typed answers that way).

## v0.24.0 — Microsoft Agent Framework flows (not tagged yet)

### Added
- **The Microsoft Agent Framework as a workflow framework.** Pick it in the workflow settings and the workflow is generated as a flow of async Python on the shared flow engine, with Agent Framework `Agent`s for the AI blocks: an `OpenAIChatCompletionClient`, tools, and a `response_format` for typed answers (`agent-framework-core` 1.20.0, `agent-framework-openai` 1.16.0). It passes the 12-case conformance kit; 22 of the 27 templates run on it.
- **Ask before a tool call, the Agent Framework way.** Tools named in *Ask before using* use `approval_mode="always_require"`. The pause saves the agent's `AgentSession` and the response holding the approval requests; the agent continues in that session with a function approval response per call (approve, edit or reject). When the model calls several tools in one turn, only the ones that need a person are asked about. Edited arguments are applied when the approved call runs.
- **Model call limits** as a chat middleware that counts an agent's model calls in one turn; a model block's temperature and token limit reach every call the same way.
- **Export** to an Agent Framework project, like the Pydantic AI and Agno ones; its tests script the model with a chat middleware, so the chat client is the framework's own and nothing reaches a provider. CI exports every template it can run and runs each project's tests.
- Servers offer it by default (`AGENTCANVAS_ADAPTERS=...,agno,microsoft_agent_framework`). Its workflows run in a worker process from the environment `make framework FW=microsoft_agent_framework` makes; the API image takes `--build-arg WITH_MICROSOFT_AGENT_FRAMEWORK=true`.

### Known limits
- The flow is not written as the framework's own `WorkflowBuilder` graph (the roadmap's "Full" tier, if users ask). A rejected tool call reaches the agent as the framework's fixed message, not the reason a person typed.

## v0.23.0 — Pydantic AI and Agno flows (not tagged yet)

### Added
- **Pydantic AI and Agno as workflow frameworks.** Pick either in the workflow settings and the workflow is generated as a flow of async Python with the framework's own agents: an agent builder per AI agent block, an `async def` per block and a `FLOW` that says which step follows which. Pydantic AI agents are `Agent`s with an `OpenAIChatModel`, `Tool`s and `NativeOutput` for typed answers (`pydantic-ai-slim` 2.54.0); Agno agents are `Agent`s with an `OpenAILike` model, tools and an `output_schema` (`agno` 3.1.1). Both pass the 12-case conformance kit; 22 of the 27 templates run on each.
- **A shared flow engine** (`adapters/supersteps.py`) runs those flows as the canvas draws them: steps side by side in the same round, Wait for all, paths by CEL rules, loops stopped at the workflow's step limit, field reducers; a pause is saved as JSON with the asking step's own framework state.
- **Ask before a tool call, the framework's way.** On Pydantic AI, tools named in *Ask before using* are `requires_approval` tools: the run pauses on `DeferredToolRequests` and continues from the saved message history with `DeferredToolResults` (approve, edited arguments through `ToolApproved(override_args=...)`, or `ToolDenied`). On Agno they use `requires_confirmation`: the paused `RunOutput` is saved and continued with `acontinue_run`. The agent's turn is not run again.
- **Model call limits** cap an agent's model calls in one turn on both (Pydantic AI's `UsageLimits`; on Agno the runtime counts them): the agent stops with a note, or the run fails when the block says so.
- **Exports** to a Pydantic AI or Agno project: the flow, the vendored engine and runtime (no LangChain), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, and tests that replay saved test cases with a scripted model. CI exports every template each can run and runs each project's tests.
- Servers offer both by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents,crewai,google_adk,pydantic_ai,agno`). Their workflows run in a worker process from the environment `make framework FW=pydantic_ai` / `FW=agno` makes (the ones mixed flows use for their agents); the API image takes `--build-arg WITH_PYDANTIC_AI=true` and `WITH_AGNO=true`.

### Changed
- The new adapters reuse the CrewAI adapter's server side through `adapters/flowadapter.py`; their worker loop, `adapters/flowworker.py`, is the Google ADK worker's made framework-neutral.

### Fixed
- The framework badges in the editor's top bar stay on one line (the bar scrolls sideways); with six frameworks they wrapped out of sight and could not be clicked.

## v0.22.0 — Onboarding and switching (not tagged yet)

### Added
- **Import from Langflow, Flowise and n8n.** Home's *Import a file…* takes the JSON those builders export (and Open Agent Spec files, as before) and creates a workflow: Langflow chat inputs, prompts, models, agents, API Request tools and If-Else routers; Flowise chatflows (LLM chains, conversation chains, tool agents, chat models, request tools) and Agentflow v2 (LLM, Agent, Condition, Human Input, Direct Reply); n8n chat triggers, AI Agent, Basic LLM Chain, chat-model sub-nodes, HTTP Request tools, If, Switch and Edit Fields with `$json` and `$('Node')` expressions. Prompts' `{variables}` become `{{ state.<field> }}`; a step that needs several earlier ones gets a Wait for all (not when they are exclusive branches); what has no counterpart becomes a placeholder step, and a note lists everything to check. `POST /v1/projects/{id}/workflows/import` returns the workflow, the `format` and the `report`.
- **Playground.** Chat with the project's default model, or any catalogue model, and its instructions before building anything; *Make it a workflow* creates a workflow with one AI agent that has those instructions and that model (`/v1/projects/{id}/playground/chat`, `/playground/workflow`).
- **Simple mode.** A *Simple / Full* switch in the editor: Simple shows only the blocks a first workflow needs and the Inspector, Copilot and Run panels, and hides the power tools; the choice is remembered in the browser.
- **Push to GitHub.** Export's *Push to GitHub…* commits the export to a repository as one commit, on an existing branch or a new one started from the default branch (`POST /v1/workflows/{id}/github`); the token is the project secret `GITHUB_TOKEN`.
- User guide: *Coming from Langflow, Flowise or n8n*, the Playground and Simple mode in the Beginner's guide, Push to GitHub in *Export and deploy*.

### Changed
- Home's *Import an Agent Spec file…* became *Import a file…* (the Agent Spec endpoint stays).

## v0.21.0 — Google ADK flows (not tagged yet)

### Added
- **Google ADK as a workflow framework.** Pick it in the workflow settings and the workflow is generated as a native ADK 2 graph workflow (`google-adk` 2.11.0): one function node per block and a `build()` that wires them with ADK's own edges (`("START", first)`, a route dict after each path block, a tuple for parallel branches, a `JoinNode` before Wait for all), ADK `Agent`s with LiteLLM models for AI agents (Python tools, typed answers as `output_schema`, model call limits as a `before_model_callback`), run inside their step with `ctx.run_node`. A run executes the generated module with ADK's `Runner`. It passes the 12-case conformance kit; 22 of the 27 templates run on it.
- **Ask before a tool call on ADK.** Tools named in an AI agent's *Ask before using* pause the run with a `tool_approval` (approve, edit the arguments, or reject), as on LangGraph and the OpenAI Agents SDK; the agent's turn runs again up to the call when the person answers. Several approvals in one turn are answered one after another.
- Approvals and questions pause an ADK workflow with ADK's own `RequestInput`; the answer continues it from the saved fields without running finished steps again. A step that loops past the workflow's step limit stops the run (`recursion_limit`).
- **Export to an ADK project:** the workflow, the runtime it needs (vendored, no LangChain), a host that reaches each model through LiteLLM over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, and tests that replay saved test cases with a scripted ADK model. CI exports every template ADK can run and runs each project's tests.
- Servers offer Google ADK by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents,crewai,google_adk`). Its workflows run in a worker process in the environment `make framework FW=google_adk` makes (the one mixed flows use for ADK agents, which now also gets `cel-python`, `jinja2` and `jsonschema`); the API image takes `--build-arg WITH_GOOGLE_ADK=true`.

### Changed
- The CrewAI adapter's worker and server side are shared: `CrewWorker` and `shared_worker` take the framework's worker files and module, and `CrewAIAdapter` names its Python, worker and code generator as methods the ADK adapter overrides.

## v0.20.0 — Mixed-flow export (not tagged yet)

### Added
- **Framework parts.** CrewAI, Google ADK, Agno, Strands Agents and LlamaIndex pin packages that clash with LangGraph's (for example `openai<3` against `openai>=3`), so their agents can no longer be installed next to the host. An agent designed for one of them now runs as a *part*: its generated module in a Python environment of its own, driven by a small worker (`part_worker.py`, standard library only) over JSON lines. Tool calls come back to the host (traced, approved, recorded), the model is the host's model bridge on 127.0.0.1. The other four frameworks keep running in the host. `scripts/check_part_envs.py` (in CI) proves which is which by resolving each framework's pins with the host's.
- **Typed contracts between parts.** Every designed agent has a contract (`interop.part_contract`, `designs.contract`): the conversation goes in as text, and its answer must match its structured output schema (or be text). An answer outside the contract stops the step with `answered outside its contract: <field>: <why>`.
- **Mixed flows on this server.** `make framework FW=<id>` sets up a framework's environment in `apps/api/.frameworks/<id>` (or `AGENTCANVAS_<ID>_PYTHON`); CrewAI's is the one `make crewai` makes. A LangGraph workflow with a CrewAI agent now runs on a server that has it.
- **One deployable project for a mixed flow.** The export keeps agents that fit in `src/<pkg>/agents/` and their packages in `pyproject.toml`, and puts each part in `parts/<agent>/` (`agent.py`, `worker.py`, `requirements.txt`, `contract.json`). `make install` and the Dockerfile install each part in its own environment (`parts/<agent>/.venv`, or `PART_<AGENT>_PYTHON`); `contracts.json` lists every part (host, own environment, or A2A) with its contract; `tests/test_parts.py` runs the flow through each part; the README explains the parts.
- **A2A parts** for agents in other languages: Remote agent (A2A) blocks are listed in `contracts.json`, and their hosts are checked by `make doctor`.
- **Mixed-framework team** template (27 templates): an OpenAI Agents SDK researcher hands its notes to a CrewAI writer whose typed brief is checked at the boundary. Exported for Docker Compose, it deploys with `make deploy` and passes `make smoke` (also a CI step).
- The AI agent's settings say when it runs as a part; `GET /v1/agent-frameworks` gains `own_env`.

### Changed
- `installed` for an agent framework now also counts a part environment; P006's hint names `make framework`.
- Exported projects' `make test` installs the parts first; CI's export job runs `make test` for every LangGraph export.

### Fixed
- A whole-workflow export with an agent designed for CrewAI, Google ADK, Agno, Strands or LlamaIndex could not be installed (its packages conflicted with LangGraph's).

## v0.19.0 — CrewAI flows (not tagged yet)

### Added
- **CrewAI as a workflow framework.** Pick it in the workflow settings and the workflow is generated as a native CrewAI flow (`crewai` 1.15.25): a `Flow` class with one method per block, chained with CrewAI's `@start`, `@listen` and `@router` (paths become `@router` methods, Wait for all listens with `and_()`, a step reached from several places with `or_()`), CrewAI `LLM`s for AI steps and `Agent`s for AI agents (Python tools, typed answers, model call limits as `max_iter`). A run executes the generated module. It passes the 12-case conformance kit; 22 of the 26 templates run on it.
- **Crew** block (`crewai.crew`, CrewAI only): a team of agents with roles, goals and backstories works through a list of tasks, in order or handed out by a manager; the last task's answer lands in a field.
- **Agent task** block (`crewai.task`, CrewAI only): one agent does one task, with an optional **guardrail** in plain words that the model checks; a failing answer is redone up to the number of tries.
- Approvals and questions pause a CrewAI flow with CrewAI's own `HumanFeedbackPending`; the answer continues it from the saved fields without running finished steps again. A step that loops past the workflow's step limit stops the run (`recursion_limit`).
- **Export to a CrewAI project:** the flow, the runtime it needs (vendored, no LangChain), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, and tests that replay saved test cases with a scripted model.
- `make crewai` installs CrewAI in its own Python environment (`apps/api/.frameworks/crewai`, or `AGENTCANVAS_CREWAI_PYTHON`); the API image takes `--build-arg WITH_CREWAI=true`. Without it CrewAI workflows can still be designed, checked and exported, and a run says how to install it.

### Changed
- Servers offer LangGraph, the OpenAI Agents SDK and CrewAI by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents,crewai`). CrewAI is not an API dependency: its flows run in a worker process (CrewAI pins `openai<3`, the API needs `openai>=3`).
- CI's api job installs CrewAI, so the conformance kit and the CrewAI tests run there; the template-export job also runs every CrewAI template export's tests.

## v0.18.0 — Model gateway and cost controls (not tagged yet)

### Added
- **Backup models** on every AI model block (`fallbacks`): when the block's model fails (provider down, out of quota, too slow), the same request goes to each backup in order and the first answer wins; a streamed answer fails over before its first token. Backups use their provider's own key and the block's temperature, output limit and timeout. Works on LangGraph and on the OpenAI Agents SDK (through the model bridge), in exported LangGraph projects (the gateway is vendored) and in exported SDK projects (`FallbackModel`). Usage and cost are counted for the model that answered.
- **Answer cache** on every AI model block (`cache_ttl`, "Reuse answers for (s)"): an identical request (messages, tools and settings; message ids ignored) within the time gets the stored answer without calling the provider, at no cost. One cache per project in the server's memory (1,000 answers).
- **Day and month budgets** for a workflow (`daily_max_cost_usd`, `monthly_max_cost_usd`): all its runs together per UTC day and calendar month; a run that spends the rest stops with `budget_exceeded`, and no new run starts until the next period.
- **Cost alerts** (`alert_at`, default 80%; reaching the budget always alerts): raised once per workflow, period and share across runs and servers (table `cost_alert`), shown as a warning in the run that crossed it (`ac.cost_alert` event), under **Spending** in the workflow settings, in the server log and as `agentcanvas_cost_alerts_total`; Prometheus rules `AgentCanvasCostAlert` and `AgentCanvasModelFailover`.
- **Spending** in the workflow settings and `GET /v1/workflows/{id}/spend`: today's and this month's spend against the budgets, runs, tokens, cached answers and backup answers, and the latest cost alerts. The run panel and each step show cached and backup answers.
- Validation: W016 (a backup repeats the model or an earlier backup); W015 now also covers backup models and day and month budgets.
- Metrics `agentcanvas_model_cache_hits_total`, `agentcanvas_model_fallbacks_total`.

### Changed
- Migration `e7a1c5f9b3d6` (`run.cache_hits`, `run.fallbacks`, table `cost_alert`).
- Run usage (`RUN_FINISHED.usage`) carries `cache_hits` and `fallbacks`.

### Fixed
- The Knowledge page showed **Load the sample handbook** for a moment while its list loaded, even when the sample was already there.

## v0.17.0 — OpenAI Agents SDK flows (not tagged yet)

### Added
- **OpenAI Agents SDK as a workflow framework.** Pick it in the workflow settings and the workflow is generated as OpenAI Agents SDK code (`openai-agents` 0.23.1): an `Agent` per AI step and AI agent, a function per block, and a `FLOW` of what follows what, run in supersteps like the canvas draws it. A run executes the generated module. Runs on the SDK: AI steps, AI agents (Python tools, tool approvals with approve, reject and edit, typed answers, model call limits as `max_turns`), model blocks of any catalog provider (through the model bridge, so usage, cost, scripted replies and recordings work as on LangGraph), Set values, Choose a path, Wait for all, Approval, Ask a person, Python steps and Search knowledge. It passes the 12-case conformance kit; 20 of the 26 templates run on it.
- **Export to an OpenAI Agents SDK project:** the flow, the runtime it needs (vendored, no LangChain), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, knowledge bases, workspace code, and tests that replay saved test cases with the SDK's scripted model (`uv run pytest`, no keys).
- **Convert and compare in Evaluation Studio.** An experiment target can run on another framework (`"framework"` on a target; **on OpenAI Agents SDK** or **+ Compare on the other frameworks** in the Experiments tab): the same workflow and dataset run on LangGraph and the SDK side by side, with scores, tokens, cost and latency per target. CI runs the Classify and route template on both in one experiment and checks equal answers, paths and token counts.
- `GET /v1/workflows/{id}/code?framework=…` previews a workflow's code converted to another installed framework.

### Changed
- Servers offer LangGraph and the OpenAI Agents SDK by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents`); `openai-agents==0.23.1` is now an API dependency.
- An AI block designed for the workflow's own framework is native there (no P001 for an OpenAI agent in an OpenAI Agents SDK flow).

### Fixed
- Agents designed for other frameworks (S21) failed to generate when an answer-shape field had a nullable type such as `["string", "null"]`; such fields are now optional.

## v0.16.0 — Knowledge Studio (not tagged yet)

### Added
- **Knowledge page and knowledge bases.** A project's knowledge bases hold documents your workflows search: upload PDF (pages kept for citations), Word, HTML, Markdown, text, CSV and JSON files (20 at a time, 20 MB each), add public web pages by address (private and internal addresses refused, also after redirects), or paste text. Click a document to see its chunks; try searches right on the page. API: `/v1/projects/{id}/knowledge`, `/v1/knowledge/{id}` (`documents`, `documents/url`, `documents/text`, `documents/{doc}/chunks`, `search`, `reindex`).
- **Chunking options:** by heading (default), paragraph, sentence or fixed windows with overlap, about 800 characters each by default. Changing chunking or the embedding model marks the knowledge base stale until **Reindex**.
- **Hybrid search with citations.** Keyword (BM25), meaning (embeddings) or hybrid (reciprocal-rank fusion of both); every passage carries a citation number, title, page and source. A built-in offline embedder needs no key (it matches word forms and misspellings); provider embedding models (`openai:text-embedding-3-small`, `ollama:…`) plug in through LangChain.
- **Search knowledge block** (`knowledge.search`, new palette category Knowledge): saves the best passages and, optionally, the citation list. Exported projects ship each knowledge base the workflow uses as `src/knowledge/<name>.jsonl` and search it the same way.
- **Retrieval evaluator** (`retrieval`): recall, precision, hit rate or MRR at k of the passages a run found against an example's `reference.relevant` (match by document, title, chunk id or a phrase).
- **Sample handbook and eval set:** 22 handbook pages of a made-up company and 50 questions with typos and paraphrases. On it, hybrid search finds the right page in the top 3 for 95% of questions against 93% for keyword search (MRR 0.94 against 0.92), measured in CI through an experiment.
- **Ask your documents** template (26 templates): search a knowledge base, answer only from the passages, cite them.
- User-guide page: Knowledge.

### Changed
- Migration `d5f9b3a7c1e4` (`knowledge_base`, `knowledge_document`, `knowledge_chunk`).
- New API dependencies: `pypdf`, `python-docx`, `python-multipart`.

## v0.15.0 — Ship to users (not tagged yet)

### Added
- **Share a deployment as an app.** Switch on a share link per deployment (Versions → **Share as an app or a chat widget**) and anyone with the link can chat with it on a clean public page (`/s/<token>`): no account, phone-friendly, light and dark, streaming answers, suggested questions, thumbs up/down, and approvals asked as Yes/No. Links can be switched off or replaced, which ends conversations started on the old link. Visitors see only their own conversations; their runs count against the deployment's rate limits and show in Runs as end user `share:<deployment>:<visitor>`. Public API `/v1/share/{token}` (info, `runs/stream`, `threads/{id}/resume`, `runs/{id}/feedback`); builder API `/v1/deployments/{id}/share` (GET, PUT, POST `regenerate`).
- **Chat widget for any website:** `<script src="https://<your AgentCanvas>/widget.js" data-share="sh_…" async></script>` adds a chat button that opens the shared app in a panel.
- **Safety blocks (guardrails):** **Hide personal data** (`guard.pii`), **Check for prompt injection** (`guard.injection`), **Check for harmful content** (`guard.moderation`) and **Check the output format** (`guard.schema`). Each checks a field or the newest message and can stop the run, or flag it (a yes/no field to branch on with Choose a path, plus details); personal data can also be redacted, masked or hashed in place. An optional AI model adds a model verdict, real moderation, or one repair of invalid JSON. Every block ships labelled eval cases (91 in all, importable as a dataset with `python -m agentcanvas.guardrails <type>`), measured at precision and recall 1.0 in CI. New warning W019 (a flagging block that saves its verdict nowhere). Exported projects run the blocks too.
- **Template gallery: 25 use-case templates** (18 new) in nine categories: getting started, customer support, content, sales, data extraction, research, HR, engineering and operations. Home shows them in a searchable gallery with category filters; each card shows category, tags, block count and difficulty. `GET /v1/templates` adds `block_count`, `category`, `tags`, `difficulty` and `sample_input`. New user-guide page: Template gallery.

### Changed
- Exports to other agent frameworks list two more gaps up front: OpenAI Agents SDK flows whose result does not come from an agent, and WayFlow flows where a Set values step writes text into a list field.
- Migrations `b3c7d1e5f9a2` (one deployment per environment, project `secrets_version`) and `c4e8a2f6b0d3` (`deployment_share`, `share_thread`).

### Milestone review after S20–S22

A whole-codebase review at the end of the framework milestone found 19 bugs; all are fixed.

### Security
- **Exports no longer run text from the workflow as code.** A workflow name is one line in exported Makefiles, compose files and scripts; secret names must be variable names (check E051, and the export refuses others); a designed agent's tool import path is checked before it is written into `agent_tools.py`; a resource may not take a name Python or the exported code reserves (check E007); an exported package is never named after a module it imports (`LangGraph`, `email`).
- **Hosted edition: no private networks.** "Allow private network" on HTTP tools and remote agents is refused (check E043) and ignored at run time, and MCP servers over HTTP must resolve to public addresses (never the cloud metadata address).
- **Release gates pin the code of code evaluators.** Saving a gate records a hash of the evaluator's workspace code (and the modules it imports); if a builder changes that code, the gate fails until an admin saves it again.
- **The Copilot's "local models only"** now also checks the model's address, and organisation policies see it.
- **Client addresses behind nginx can no longer be forged** with an `X-Forwarded-For` header: nginx believes it only from `TRUSTED_PROXIES` (Helm `web.trustedProxies`) and passes the API one address, so rate limits and the audit log use the real client.
- Project secrets can only be stored under variable-safe names.

### Fixed
- Secrets changed on one API replica reach the others: each run reloads its project's secrets when they changed, and every replica re-syncs every 10 seconds (so new values are also masked there).
- Event handlers can resume or continue the thread of the run that triggered them, and streams and `/runs/wait` no longer wait for handlers to finish.
- Two publishes at the same moment can no longer create two deployments for one environment (a migration merges any existing duplicates).
- Retrying a failed remote (A2A) agent step contacts the remote agent again instead of replaying the failure.
- Pending tool approvals of designed agents are bounded in memory.
- Editor: opening another workflow, reloading after a conflict or restoring a version clears undo/redo; a late save of the previous workflow, or its still-streaming run, no longer lands in the new one.
- Workspace: reopening a file after saving it shows the saved text, so the next save cannot overwrite it with the old one.
- Exported services forget idle conversation locks.
- Accessibility: labels on the state designer's selects and the rejection reason box.

## v0.14.0 — Whole flows, preview (not tagged yet)

### Added
- **Export a whole workflow to another framework (preview).** The Export box has a **Framework** choice: besides the AgentCanvas service, **LangGraph (through Agent Spec)**, **WayFlow** and **OpenAI Agents SDK**. The workflow is written as an Open Agent Spec flow and that framework's own Agent Spec loader runs it. The download holds `flow.json`, a runner, `steps.py` for the steps only AgentCanvas runs ("Set values" steps are written for you), pinned packages, a README, `GAPS.md` and tests against a fake model server. `GET /v1/workflows/{id}/bridges`, `GET /v1/workflows/{id}/bridges/{target}/export.zip`.
- **Gaps report.** Before downloading, the Export box lists what stops the workflow from running on that framework and what works differently there; the same list ships in `GAPS.md`.
- **CI runs the exports for real**: every template exported to each framework; the exports' own tests pass, and 4 templates run end to end on LangGraph and WayFlow, 2 on the OpenAI Agents SDK.

### Changed
- Agent Spec export: steps AgentCanvas runs read the state their settings use, a typed agent's answer is its output, outputs that a path may skip have defaults, the conversation is no longer a flow input, and prompts with template logic are listed in the report.
- The fake model server in exported tests also calls a tool the request requires.

## v0.13.0 — Design any agent (not tagged yet)

### Added
- **Design an AI agent in any of nine agent frameworks.** An AI agent block's **Designed for** now lists agent frameworks: OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands Agents, LlamaIndex, Microsoft Agent Framework and Claude Agent SDK. The block is generated as that framework's own code (instructions, model, workspace/HTTP/MCP tools, tool approvals, a typed answer as a Pydantic class) and runs as that code. `GET /v1/agent-frameworks`.
- **Model bridge.** A designed agent's model calls go to a local, per-run endpoint that speaks the OpenAI chat-completions and Anthropic messages APIs (streaming included) and answers with the model wired into the block, so runs stay traced, costed, mocked and replayable, and API keys never reach the agent's code.
- **View the code and take it away.** **View … code** in the block's settings (`GET /v1/workflows/{id}/agents/{node}/code`, `…/code.py`) and **Export as … project** (`…/export.zip`): the agent, its tools, `main.py`, pinned packages, a README and tests that run against a bundled fake model without an API key. Whole-workflow exports put each designed agent in `agents/` and add its packages.
- **Checks** P005 (the framework cannot take something wired into the agent: add-ons, other tool kinds, its answer shape) and P006 (the framework is not installed on this server). Workflow frameworks without the new `agent_designs` ability (Echo) cannot run designed agents (P001), and their badge says so.
- The Copilot knows the agent frameworks and suggests designing an agent in one when asked.
- CI runs each framework's designed agent (plain, typed answer, approval) and its exported project's tests against the real package.

### Notes
- A tool approval pauses the run; on approval the designed agent's step starts again from its beginning, and the decision applies only if the agent asks for exactly the same call again.
- Structured output uses each framework's own mechanism, so typed answers may differ slightly between frameworks.

## v0.12.0 — Framework foundation (not tagged yet)

### Added
- **Pick a workflow's framework.** Workflow settings (click empty canvas) → **Framework** lists the frameworks this server offers; switching is an undoable edit (`UpdateMeta.framework`). Runs, conversation state, history, the code view and exports use that framework's adapter.
- **Badges for where a workflow can run.** One badge per installed framework next to the workflow's name: ✓ when every block can run there, or how many blocks cannot (point at it to see which, click it to select the first). `GET /v1/frameworks`, `GET /v1/workflows/{id}/portability`.
- **Portability checks** P001 (the framework cannot run this block, or is not installed), P002 (it only imitates the block), P003 (which other installed frameworks the workflow could move to) and P004 (a block designed for another framework), with plain-language help.
- The Problems panel says "No problems" when only info notes remain; the notes are still listed.
- **IR 1.2: a block's own framework.** AI steps, AI agents and Planning Agents can be **Designed for** another framework (`engine`); until mixed-framework flows arrive they run on the workflow's framework. Older workflows load unchanged.
- **Conformance kit** (`agentcanvas.conformance`): sequence, three-way paths, parallel branches joined, a bounded loop, AI replies that fill a field or pick a path, approve, reject, approve with an edit, and a question re-asked after an invalid answer, plus checks on the events every run must send. Every adapter runs it in CI.
- **Echo demo engine** (`AGENTCANVAS_ADAPTERS=langchain,echo`): a tiny second framework that passes the kit, to prove the seams. Off by default; not for production.

### Changed
- **Adapter interface v2** (ADR 0018): a required core (generate, package, run with canonical events) and optional abilities each adapter declares (conversation state, history and replay, long-term memory, connector browsing, breakpoints). What a workflow's framework lacks is refused with a plain message instead of failing.
- **Model calls outside workflows** (the Copilot, LLM judges, the setup page's connection test) go through a framework-neutral model service, not a workflow adapter.

### Fixed (review of v0.11.0)
- A workflow's own Script steps (`flows.<workflow>.scripts.…`) ran in the server process even when the project has a sandbox; they now go to the sandbox like shared scripts.
- The Copilot's copy of the user guide had missed the v0.11.0 Workspace, Export and Copilot pages.

## v0.11.0 — Workspace and editor fixes (not tagged yet)

### Added
- **Each workflow has its own code.** The Workspace opened from a workflow (`/workflows/{id}/workspace`) shows that workflow's files (`flows/<id>/scripts/`, `tools/`, `nodes/`) and, separately, the shared files every workflow can use (`scripts/`, `tools/`, … at the workspace root, unchanged). **+ Create the code file** on a Script step now writes into the workflow's own folder (`flows.<id>.scripts.<file>:run`); the palette shows only this workflow's tools and scripts and the shared ones. `GET /v1/projects/{id}/workflow-files/{wf_id}`; the catalog takes `workflow_id`.
- **Workspace code imports other workspace code** (`from scripts.textutil import shout`, `from .helpers import x`) the same way in a run, in the sandbox and in an export.
- **Copilot help in the code editor.** Under a Python file: describe what the code should do and **Write with Copilot**, **Explain** the file or the selected lines, or **Fix** the problems ruff found. Changes come as a diff to **Accept** (an unsaved, undoable edit) or **Reject**; nothing is saved without you. Uses the project's Copilot model and privacy setting. `POST /v1/workflows/{id}/copilot/code`.
- **Starter files.** **New file** and the "+ Script step", "+ Tool for the AI" and "+ Custom block" chips start from working code for that kind of file.
- **A way back.** The Workspace says which workflow it belongs to and has **← Back to *workflow name***; going back (or the browser's Back button) returns to the same block selected and the same canvas position.

### Changed
- **Exports bundle exactly the code the workflow uses**: its own folder's code, the shared modules it imports (found by following imports), data files in its folder, and the packages in `requirements.txt` (workspace root or the workflow's folder). Other workflows' code is left out. A generated `tests/test_workspace_code.py` checks that every code path the workflow names imports inside the export, and an export whose code is missing from the workspace stops with a message naming it.

### Fixed
- **List fields in block settings** (such as "Tools that need approval" in the AI agent block) showed empty buttons and a new item failed with `Instance type "null" is invalid`. The buttons now read **+ Add**, **Remove**, **↑ Up** and **↓ Down**, and a new item starts empty. The same applies to decision cards in the Inbox.
- **Every button has a label**: Undo and Redo got names, and a browser check fails on any visible button without one, on every editor tab, dock panel and page.

## v0.10.0 — Any framework as a node, and a UI refresh (not tagged yet)

### Added
- **Agents from nine other frameworks as steps.** The **Agent from another framework** block runs an agent from your Workspace built with the OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands Agents, LlamaIndex Workflows, Microsoft Agent Framework or the Claude Agent SDK (or a plain Python function) as one step: prompt from workflow fields, answer into a field or the chat, optional approval before it runs, input and answer in the trace. A factory function gives a fresh agent per run. Exports vendor the step and add the framework's pinned package.
- **Remote agents.** **Remote agent (A2A)** calls any Agent2Agent agent by its Agent Card address; when it asks for more information the run pauses for a person and the answer goes back to the same task (no message is sent twice on resume). Private addresses are refused unless allowed. **Remote agent (MCP)** calls one tool of a connected MCP server as a step.
- **Safety for remote agents:** the A2A step connects to the address it checked (no DNS rebinding to private networks), and after a server restart a step paused on a remote question stops with a message instead of sending its first message again.
- **Framework badges** on these blocks name the framework (or A2A, MCP).
- **Open Agent Spec import and export.** **Versions → Download as an Agent Spec file** writes the workflow as an Agent Spec flow (`LlmNode`, `AgentNode`, `A2AAgent`, `BranchingNode`, `InputMessageNode`, tools) and lists what Agent Spec cannot express; **Import an Agent Spec file** on the home page builds a workflow from any Agent Spec flow or agent and lists what it approximated. Files AgentCanvas wrote import back exactly, layout included; header values typed in directly are never written to the file. `GET /v1/workflows/{id}/agent-spec`, `POST /v1/projects/{id}/workflows/agent-spec`.
- **CI `frameworks` job:** a real agent from each of the nine frameworks, at the pinned version, runs as a canvas step against a fake model server (OpenAI and Anthropic APIs), and every template's Agent Spec export loads in pyagentspec.

### Fixed (review of the v0.9.0 deploy kit)
- Azure Container Apps deployed the literal text "unset" for empty settings; the deploy script now stops and names the missing ones.
- Cloud Run's service account could not read its secrets; the script grants it access (and `GCP_SERVICE_ACCOUNT` picks another account).
- Placeholder API keys ("unset", "none", "changeme") no longer count as configured in `make doctor` or the exported service.

### Changed (UI refresh, #20)
- **A new look.** A redesigned interface across every page: a frosted top bar with grouped canvas tools, softer cards and panels, clearer tabs, rounder canvas blocks with status glows, and themed minimap, controls and tooltips.
- **Light, Medium and Dark themes**, plus System (follows your computer), from the switch in the top bar; remembered in this browser.
- **Copilot chat** has a proper chat layout: avatars, chat bubbles, a typing indicator, a welcome screen with suggestions, and a composer with the Build/Ask switch and Send inside it.

## v0.9.0 — Export deploy kit (not tagged yet)

### Added
- **Export to run in your own environment.** Pick a version and where it will run (your computer with Docker, a Linux server, Kubernetes, Google Cloud Run, AWS ECS Fargate or Azure Container Apps). The export dialog lists what that needs (tools, keys, outside services, database, size) before you download. `GET /v1/workflows/{id}/export.zip?target=…`, `GET /v1/workflows/{id}/export/plan?target=…`. See `docs/user-guide/export.md`.
- **The export is a service** with the same API as an AgentCanvas deployment (`runs/wait`, `runs/stream`, `threads/{id}/resume`, `runs/{id}/feedback`, `healthz`, `docs`) behind API keys; conversations in Postgres (`DATABASE_URL`) or SQLite. It refuses to start, with a plain message, when settings are missing.
- **Deploy files for the chosen target only:** Docker Compose (with Caddy for automatic HTTPS on a server), a Helm chart, or a Cloud Run, ECS or Container Apps script that keeps the keys in the platform's secret store. The Dockerfile runs as a non-root user with a health check and pinned base images.
- **Four guides written for the workflow:** prerequisites, setup, deploy, verify and operate (smoke test, logs, scaling, upgrade, rollback, backups, key rotation), with its keys, hosts and settings named.
- **`make doctor`** checks tools, settings, database, outside services and the port (and with `--live`, each model key), pointing each problem at the guide section that fixes it; **`make smoke`** calls the deployed service once; **`make gate`** runs the saved test cases and the service's API tests. `DEMO_MODE=true` answers with canned replies for a first try without keys.
- **CI** exports every template to every target, lints the deploy files, runs each export's own tests with its own pinned packages, and deploys exports for real with Docker Compose and on Kubernetes (kind).

### Notes
- The exported service has the deployment API (runs, resume, feedback), not the MCP and A2A addresses added in v0.7.0.
- Built ahead of S16–S17: release numbers follow merge order.

### Fixed (review against v0.7.0)
- A resume in the exported service ran without the paused run's context, so user-scoped memory failed and `context.*` prompts were empty after an approval.
- Feedback in the exported service only found runs answered by the same process (404 on another replica or after a restart).
- `make smoke` and the export's own tests failed for workflows with a required input context field.

## v0.8.0 — AI Copilot II and debug assistant (not tagged yet)

### Added
- **The Copilot tests.** It turns a brief's success criteria into test cases, tries the workflow as it would be with its proposal (dry runs: risky tools are stubbed; scripted model replies when no key is set), fixes what fails, and saves the tests when you accept.
- **The Copilot debugs.** It reads the Problems list with a plain explanation of every code, recent runs and what each step did, explains causes in plain words, and checks a fix by rerunning the failed run's input with its recorded model replies and tool outputs. **✦ Ask the Copilot to fix these** (Problems) and **✦ Ask the Copilot why** (run errors) hand the problem over.
- **One error catalog.** Plain explanations of validation codes and common run errors live in `agentcanvas/validation/help.json`, used by the editor and the Copilot (`make schema` and `pnpm gen` copy it).
- **Guide search.** The Copilot searches the user guide (shipped inside the API package).
- **Project Copilot notes**, a **model for questions** (split mode) and **Private** mode (local models only) in Copilot settings.
- **Debug eval suite.** `python -m agentcanvas.copilot.evals --suite debug` plants about 30 bugs in the starter templates and checks the Copilot's fixes.

### Fixed (review against v0.7.0)
- A follow-up Copilot turn started from the saved draft: a tests-only or notes-only proposal replaced an open proposal and dropped its changes, and test runs did not see the open fix. A turn now starts from the open proposal.
- A new or changed local (stdio) app connector could start its program during a Copilot test run before the person accepted it.
- Reproductions replaced recorded tool output with the dry-run stub for risky tools.
- Past runs of a deployment were explained against the draft instead of their version.
- A question model that is not allowed (policies, local-only mode, no tool calling) was accepted in settings and then failed on every question.

## v0.7.0 — Open doors (not tagged yet)

### Added
- **Every deployment is an MCP server.** `POST /v1/deployments/{id}/mcp` (Streamable HTTP, the deployment's key) offers one tool named after the workflow, with its input fields, and `answer` for runs that pause for a person. Claude Desktop (through `mcp-remote`), Cursor, ChatGPT and agent frameworks can call published workflows. The publish panel shows the address and a ready-to-paste Claude Desktop setting.
- **Every deployment is an A2A agent.** Agent card at `/v1/deployments/{id}/a2a/.well-known/agent-card.json`; `POST /v1/deployments/{id}/a2a` takes `message/send` (data parts are input fields, text goes to the workflow's text field) and `tasks/get`; a pause is `input-required` and is answered on the same task.
- **App connector gallery.** About thirty ready-made MCP servers (GitHub, Slack, Notion, Postgres, a browser, web search, …) in the block list under **App connectors**; each becomes a filled-in App connector block. `GET /v1/mcp-connectors`. Local (stdio) connectors take environment variables, which can be secrets.
- **Trace export.** An admin sends every run of a project to Langfuse, Arize Phoenix, Datadog (through the Agent's OTLP receiver) or any OTLP/HTTP endpoint, as OpenTelemetry traces with steps, model calls (tokens, replies) and tool calls. Secrets are masked and `trace_redact` fields redacted; content can be left out. `GET/PUT/DELETE /v1/projects/{id}/trace-export`, `POST …/trace-export/test`.
- The Copilot knows which framework a workflow targets.

### Fixed
- Chat models are cached per configuration: changing a model, its key or its address (including the Copilot's model) took effect only after the API restarted, and same-named models in different workflows of a project could share one client.
- Two Copilot turns that overlapped on different API processes left two open proposals; the older one is now superseded.
- A signed webhook for a deleted workflow was refused as "already received" (409); it now gets 404.

## v0.6.0 — AI Copilot (not tagged yet)

### Added
- **AI Copilot in the editor.** Open **✦ Copilot**, describe the workflow you want, and the Copilot writes a brief (goal, inputs, outputs, steps, tools, success criteria, open questions), says its plan, and builds it with edit commands, fixing its own validation errors. Its changes are previewed on the canvas (new in green, changed in amber) and applied only when you **Accept**, as one undoable edit. **Ask** mode answers questions without changing anything and is open to viewers. See `docs/user-guide/copilot.md`.
- **The Copilot knows the product.** A knowledge pack generated from the running product (guide, every block's ports and settings including workspace blocks, starter templates, edit commands) is sent with each message and cached by providers that support prompt caching; tools look up block details, templates and the workflow with its problems.
- **Copilot model choice.** The workflow's own model (project default, else the workflow's first AI model) or a separate model, with a token limit per conversation; organisation model policies apply to it. `GET/PUT /v1/projects/{id}/copilot-settings`; conversations at `/v1/workflows/{id}/copilot` (`POST …/messages` streams Server-Sent Events).
- **Copilot eval harness.** `python -m agentcanvas.copilot.evals --model …` builds ten workflows from plain requests and checks each validates and uses the expected blocks.
- **Any model provider, including local models.** Chat models can use Anthropic, OpenAI, Azure OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, any OpenAI-compatible API (`openai_compatible:` with a Base URL), and models on your own computer: Ollama (`ollama:`), LM Studio (`lmstudio:`), vLLM (`vllm:`) and llama.cpp (`llamacpp:`). Every integration is installed with the API. `GET /v1/model-providers` lists them, with each one's key variable, default address and whether it is installed.
- **API keys per provider, from the canvas.** A project secret named after the provider (e.g. `MISTRAL_API_KEY`) is used by that provider's models when their config names no key; the Chat model's **API key** field picks any project secret by name. The model field shows where the key comes from.
- **First-run setup.** Home asks for the model to use: pick a provider and model, enter the key or a local server's address, **Test connection**, and **Save**. The key becomes a project secret and the model the project's default, which new workflows from templates use. `GET/PUT /v1/projects/{id}/setup`, `POST …/setup/test`, `POST …/setup/skip`.
- **Run everything in Docker.** `make docker-up` (`infra/compose.local.yml`) builds and starts Postgres, the API and the web app on http://localhost:7777 without sign-in; local model servers on the host are reached through `host.docker.internal` (`AGENTCANVAS_LOCAL_MODELS_HOST`).
- **User guide** in `docs/user-guide/`: running AgentCanvas on your computer, and product documentation for every area.
- **Help for beginners on the canvas.** Hover any dot on a block to see what it carries and what to do with it. Click a dot to see everything that can connect there (steps that can come next, or the add-ons that fit an input) and add it already connected, in one click. Every problem in the Problems list and the Inspector says *what happened* and *how to fix it* in plain English, refused connections explain what to do instead, and failed runs explain the likely cause (API key, local model not running, misspelt field…). A beginner's guide opens the first time you open an empty workflow and is always one click away (**❓ Help**); `docs/user-guide/beginner-guide.md` has the same guide.

### Changed
- LLM judges, pairwise judges and simulated users accept any known provider's `provider:model`, not only catalog models.
- **Plain block names.** The palette uses everyday names and descriptions: *Ask AI* (was LLM call), *AI Agent*, *Planning Agent* (Deep Agent), *Helper Agent* (Subagent), *AI model* (Chat model), *Choose a path* (Router), *Repeat for each item* (Map), *Wait for all* (Join), *Group of steps* (Subgraph), *Ask for approval* (Approval), *Ask a person* (Human input), *Run Python code* (Python script), *Web API tool*, *App connector (MCP)*, and plain names for memory blocks and agent add-ons (middleware). Categories are ordered for beginners and every port has a description. Type ids (`core.llm`, …) are unchanged, so saved workflows and exported code are unaffected; new blocks get names from the new labels (e.g. `ask_ai`). Start and END show as *Start* and *Finish*. A new AI model block starts as the project's default model.
- New diagnostics: `W011` (a provider's integration is not installed on this server) and `E017` (an OpenAI-compatible model without a Base URL).

## v0.5.0 — Evaluation in production (not merged yet)

### Added
- **Online evaluators.** Attach any of a workflow's evaluators to a deployment with a sampling rate (0-100 %) and a filter (run status, the metadata the API caller sent). Sampled runs are scored in the background after the response is sent, so callers never wait. Scores show in the run list and on each run's timeline, with hourly and daily averages on the Evaluation page's *Production* tab. The judge's own cost is counted apart from the run's. `GET/POST /v1/deployments/{id}/online-evaluators`, `GET /v1/deployments/{id}/scores`, `/scored-runs`, `GET /v1/threads/{id}/scores`.
- **Drift alerts.** An online evaluator can alert when its mean over a recent window falls below a value, or drops more than a set amount below its trailing 7-day mean. Open alerts show in the app (`GET /v1/workflows/{id}/alerts`) and on `/metrics` as `agentcanvas_eval_alert_open`; `infra/observability/alerts.yaml` ships the `AgentCanvasEvaluationDrift` rule.
- **Annotation queues.** A queue per workflow is fed by rules (an online score below a threshold, a failed run, a thumbs-down) or by hand from any run. Reviewers see the input, output and timeline, score each rubric criterion, comment, and can add the run to a dataset with a corrected reference in one step. Queue progress and agreement between reviewers are shown on the *Queues* tab.
- **Feedback from API callers.** `POST /v1/deployments/{id}/runs/{run_id}/feedback` with `{"rating": "up" | "down", "comment"}`; a thumbs-down goes to the queues that ask for it.
- **Pairwise and composite evaluators.** A pairwise LLM judge compares two experiment targets on each example, asking twice with the answers swapped, and reports a win rate with a confidence interval. A composite evaluator combines others by weights or all/any rules. Both work in experiments and release gates (a pairwise gate compares the candidate with the live version).
- **Rubric self-grading.** A Rubric widget on agent and Deep Agent steps lets a grader model check the answer against your rubric and ask for revisions, up to a set number of rounds. Each grading round shows live in the run panel and is kept on the timeline; exported code carries it.
- **Simulated users.** For chat workflows, an experiment can hold a whole conversation per example with a simulated user (a catalog model given a persona, a goal and a turn limit). Each conversation is one result with its full transcript, which evaluators see.
- **LangSmith dataset sync.** Push a dataset version to LangSmith, or pull its current examples back as a new version, from the dataset page (`POST /v1/datasets/{id}/langsmith/push`, `/pull`). It uses the project secret `LANGSMITH_API_KEY` (or the server's) and only syncs when you ask.
- **Record/replay covers tools.** A recorded run now keeps what each HTTP, MCP and workspace tool was asked and answered. A regression test made from it replays those answers instead of calling the tools, so it runs without network access or side effects.

### Changed
- Fields listed in `settings.trace_redact` are also redacted in the root run and model runs sent to LangSmith and OpenTelemetry.
- A release gate keeps the dataset version and evaluator settings it was saved with, so later edits do not change what it measures until an admin saves it again.

### Fixed
- An API instance that stalls long enough for another to take over its runs or experiments now stops its copy instead of finishing it twice.
- Dry runs also stand in for workspace tool functions with a risky effect; they used to call them.

## v0.4.0 — Evaluation Studio (merged 2026-10-06, not tagged yet)

### Added
- **Datasets** (API) to evaluate a workflow on: build them from test cases, past runs, CSV or JSONL files; every change is kept as a version. `GET/POST /v1/workflows/{id}/datasets`, `/v1/datasets/{id}/…`.
- **Evaluators** (API) that score a run: exact match, regex, JSON Schema, an LLM judge with your rubric, tool-call trajectory, or your own function in `scripts/`. Each gives a 0-1 score, pass or fail at a threshold, and a comment. `GET/POST /v1/workflows/{id}/evaluators`, `POST /v1/evaluators/{id}/try`.
- **Experiments** (API): run the draft and published versions, or variants with another model or prompt, over a dataset version in the background, and compare mean scores, pass rates, latency, tokens and cost. An experiment can be cancelled and carries on after a redeploy. `POST /v1/workflows/{id}/experiments`, `/v1/experiments/{id}`, `/v1/experiments/{id}/results`.
- **Evaluation page** (editor → *Evaluate*): build datasets and evaluators, run experiments, read the results grid with each run's timeline one click away, and compare two experiments to see what improved or regressed.
- **Release gates.** An admin can require an evaluation before publishing to an environment: minimum mean scores on a dataset and how far the pass rate may drop against the live version. Publishing there evaluates the exact workflow (or reuses an evaluation that already did) and is refused on a miss; admins can publish anyway with a reason that the audit log keeps.
- **Rate limits per caller.** The public API limits each deployment key (120/min), webhook (120/min) and project (600/min) across all API instances, and answers 429 with `Retry-After` beyond that. See `AGENTCANVAS_RATE_LIMIT_*`.

### Changed
- The daily retention job deletes audit-log rows after 400 days (`AGENTCANVAS_AUDIT_RETENTION_DAYS`, 0 keeps them) and, if you set `AGENTCANVAS_MEMORY_RETENTION_DAYS`, long-term memories that were not updated for that long.

### Security
- The web app no longer needs `'unsafe-eval'`: forms validate without generating code, and the Content-Security-Policy is `script-src 'self'`.
- Base images are pinned by digest; workspace files that are symlinks out of the workspace are not listed.
- Release gates hold against builders: changing or deleting a gating dataset, or an evaluator of a gated workflow, needs an admin (403 otherwise), like the gate itself.
- Publishing pins exactly what the gate evaluated: if the draft or the workspace changes during the evaluation, the publish is refused (409). Evaluations are reused only for the same workspace commit.
- Regex evaluators run in a worker process with a 2 s limit, so a pathological pattern cannot freeze the API; code evaluators time out after 60 s.
- Masking also covers run errors sent to LangSmith, span status messages, and secrets with quotes, backslashes or non-ASCII characters.

### Fixed
- Secret values no longer reach LangSmith traces or OpenTelemetry spans; they are masked as `***`. A workflow can also name fields (`settings.trace_redact`) whose values are never traced.
- An example whose run never started now fails every evaluator instead of being left out of averages and gates, and a gate fails when not every example finished. A non-finite score (NaN) no longer counts as a pass.
- Trying an evaluator uses the workflow's own project for model keys and workspace code.
- The Evaluation page no longer crashes when switching experiments with an output diff open; forms accept Python-style regular expressions (for example in the PII middleware) again.
- Runs of an API instance that stopped without a clean shutdown (out of memory, a lost node) no longer stay "running": another instance picks them up after 60 s (`AGENTCANVAS_ORPHAN_AFTER_S`).

## v0.3.0 — Production readiness (merged 2026-10-06, not tagged yet)

### Production readiness
- **Containers, compose and Helm.** Non-root API and web images, `infra/compose.prod.yml` (two API replicas, a one-shot migration) and a Helm chart in `infra/helm/agentcanvas`. `/readyz` checks the database and the run store.
- **Observability.** OpenTelemetry traces for requests, database calls and outgoing HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; Prometheus metrics on `/metrics`; a Grafana dashboard and alert rules in `infra/observability/`.
- **Retention and backups.** Conversations older than the retention period are deleted daily; backup and restore scripts with a runbook.
- **Safe redeploy.** On shutdown an instance stops taking runs, lets running ones reach a step boundary and hands them to another instance, which resumes them. Threads move to a newly published version only when it is resume-compatible.
- **Load tested.** 200 concurrent streaming runs on compose prod: 5,875 runs, none failed, first event p95 89 ms.
- **Secrets under a master key.** Each organisation's secrets use their own data key, wrapped by a key file, AWS KMS or Google Cloud KMS (`AGENTCANVAS_KMS_KEY`). Rotate with `python -m agentcanvas.security.kms rewrap`.
- **Encrypted checkpoints** when `LANGGRAPH_AES_KEY` is set.
- **Security review** completed (`10-security-checklist.md` §7). Dependency and image scanning run in CI; the web server sends a Content Security Policy and rate-limits deployment and webhook calls.

### Changed
- **Webhook signatures include a timestamp.** Senders send `X-AgentCanvas-Timestamp` and sign `"<timestamp>." + body`; deliveries older than five minutes are refused. Update existing senders before upgrading.
- Invitations to someone who already belongs to another organisation wait until they accept them (Home shows a banner). Members who have not accepted show no profile details.
- You start in the organisation you used last.
- The web app asks for `offline_access` and renews sessions silently; enable refresh tokens at your identity provider.
- The HTTP tool refuses private, loopback and link-local addresses unless the tool allows private networks.
- The docker sandbox runs as an unprivileged user and is killed on timeout.
- Production refuses the development secret key, and the hosted edition refuses to start without a sandbox. API docs are off in production.

## v0.2.0 — Accounts & access (merged 2026-10-05, not tagged yet)

### Accounts & access
- **Sign-in with your identity provider (OIDC).** Set `AGENTCANVAS_AUTH_MODE=oidc`; the web app signs in with authorization code + PKCE and the API checks every token. Deployment API keys and signed webhooks work as before.
- **Organisations.** Everything belongs to an organisation, and nobody sees another organisation's workflows, runs, secrets or deployments. Create projects with `POST /v1/projects`.
- **Roles:** viewer, reviewer (answers approvals), builder and admin. Admins invite people by email from the new Members page.
- **Audit log.** Every change is recorded with who made it, what it touched and from where; approvals include a hash of what was approved. Admins read it on the Audit log page.
- **Policies.** Admins write organisation rules as CEL on the new Policies page, for example "every external write needs approval" or "only approved models in prod". The editor flags workflows that break them (`E095`), and publishing refuses them in a new "policies" checklist item. Rules for one environment show as warnings in the editor (`W095`) and block publishing there. Steps inside subgraphs are checked too.
- **Memory per organisation.** Long-term memory is kept per organisation, and the Memory inspector shows only your organisation's. Memories saved before this release move to the default organisation the first time the API starts.

### Changed
- Downloads and the "Open trace" link now go through the signed-in API (`GET /v1/runs/{id}/trace?format=json`).
- Requests that name an id from another organisation, or one that doesn't exist, as a query parameter (`?workflow_id=…`) now answer 404 instead of an empty list.
- Policies are enforced when publishing. Draft runs, debug runs and test-case runs are no longer blocked by a broken policy.
- Deployment API end users are kept apart from organisation members: a `user_id` passed to a deployment becomes `api:<deployment>:<user_id>` in the run context and in user-scoped memory.
- Memories from before organisations are moved once; the API records that it has done so and never moves memories again.

### Fixed
- Checking policies took seconds on large workflows (about 6 s for 120 steps); it now takes a fraction of a second.
- A workflow with a malformed embedded subgraph no longer fails to open (500) once the organisation has a policy.
- Reviewers can resume runs but can no longer add debug pins, mocks or dry runs to them.
- An organisation can no longer be left without an admin who has signed in, including through concurrent role changes.
- The web app renews an expired sign-in in the background, has Sign out and "Sign in with a different account", and hides actions your role cannot take.

## v0.1.0 — MVP (2026-10-05)

The first usable release. You can build an agentic workflow on a canvas, run and debug it, test it, export it as plain Python and publish it behind an API.

### Build
- **Canvas editor** (React Flow):
  - typed, colour-coded ports
  - connections are checked before they are made, and a refusal says why
  - autosave with conflict detection, undo/redo
  - copy/paste, auto-layout (Tidy), ⌘K command palette, multi-select
- **Workflow format.** The workflow IR (v1.1) is the single source of truth: one JSON document, validated by about 70 diagnostic rules shown in the Problems dock.
- **Widgets:**

  | Group | Widgets |
  |---|---|
  | Steps | LLM, Router, Set, Script, Approval, Human input |
  | Agents | Agent (with middleware and structured output), Deep Agent with sub-agents and file backends |
  | Structure | Map/Join (parallel fan-out), Subgraph |
  | Memory | recall / remember / forget |
  | Tools | HTTP tool, MCP server, memory tools, workspace tools |
  | Middleware | PII, call limits, retry, fallback, summarisation, to-dos, tool selection |

- **Workspaces.** Each project has its own git-backed workspace with a code editor and lint markers. It holds:
  - script nodes and custom tools
  - custom widgets (`@canvas_node`)
  - event handlers (`@on`)
- **Sandboxes.** Workspace code runs in-process, in a subprocess, in Docker, or (experimental) in a LangSmith Sandbox. The policy file `sandbox.json` sets limits, an egress allow-list and header injection.
- **Secrets.** Encrypted at rest, write-only through the API, and masked in every run event.
- **Templates:** Ticket triage, Approval workflow, Data extraction, RAG Q&A, Research brief, Classify & route, Chat assistant.
- **Guided build.** A six-step first-run tutorial that advances as the real canvas takes shape.

### Run & debug
- **Durable runs.** Runs survive a restart.
- **Human decisions.** Approvals and inputs appear in the Run panel and in a cross-workflow Inbox.
- **Run panel:**
  - breakpoints
  - a timeline with state edits, fork and replay
  - Explain mode, which narrates each step
  - live token streaming
- **Live Code Mirror.** The generated LangGraph code updates as you edit.
- **Debug aids:**
  - pinned outputs and mock models
  - cost, tokens and latency per step
  - budgets per run and per conversation
  - channel lineage
- **Blast-radius overlay.** Shows which steps can write, send, execute or spend, and whether a human approves first. A **dry run** stubs every risky tool with a recorder.
- **LangSmith tracing** with an "Open trace" link.

### Ship
- **Versions.** Semver snapshots of the workflow and its workspace code, a field-level diff shown on the canvas, and restore.
- **Saved test cases:**
  - assert on final status, output values and route
  - run against the draft or any version
- **Record/replay.** Turn any recorded run, including production runs, into a deterministic regression test.
- **Export** as a standalone Python project:
  - generated graph, vendored node library, workspace code
  - pinned dependencies, Dockerfile, `langgraph.json`
  - the project's own tests pass with `uv run pytest`
  - it serves under `langgraph dev`
- **Publish:**
  - a pre-publish checklist: validation, tests and secrets; breaking contract changes are reported
  - one deployment per environment behind a rotatable API key
  - endpoints `runs/wait`, `runs/stream` and resume, always pinned to the published version
- **Triggers.** HMAC-signed webhooks and cron schedules.
- **Contract view** of a workflow's API, with breaking-change detection between versions.

### Quality
- **Tests:**

  | Suite | Count |
  |---|---|
  | API, on SQLite and Postgres | about 200 |
  | Web unit tests | 47 |
  | Playwright journeys | 27 |

- **`make check`** runs every CI gate locally except the Playwright journeys (`make e2e`) and the strict docs build.
- **UI states.** Every screen has loading, error-with-retry and empty states. Error boundaries keep one broken panel from blanking the app.
- **Bundle size.** The web entry bundle is 0.3 MB: pages and the layout engine load on demand.

### Known limitations
- **No real authentication yet.** Every request acts as a built-in development admin. OIDC, organisations and roles arrive in v0.2.0.
- **Cron runs on one API instance only.** Multi-instance deployments need the leader lock planned for v0.3.0.
- **Record/replay replays model replies only.** Tools run again during replay.
- **The LangSmith Sandbox mode is unmeasured.** It has not run against the live service (see spike SP-2).
