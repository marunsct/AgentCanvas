-- AgentCanvas control database (MVP, milestones M1–M7). Checkpoints/store tables are created by LangGraph (.setup()).
CREATE TABLE organization (
  id            text PRIMARY KEY,                 -- org_<ulid>
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
  id            text PRIMARY KEY,                 -- usr_<ulid>
  oidc_subject  text UNIQUE NOT NULL,
  email         text NOT NULL,
  display_name  text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE membership (
  org_id        text NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  user_id       text NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  role          text NOT NULL CHECK (role IN ('admin','builder','reviewer','viewer')),
  PRIMARY KEY (org_id, user_id)
);

CREATE TABLE project (
  id            text PRIMARY KEY,                 -- prj_<ulid>
  org_id        text NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE workflow (
  id            text PRIMARY KEY,                 -- wf_<ulid>
  project_id    text NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name          text NOT NULL,
  draft_ir      jsonb NOT NULL,                   -- current editable IR
  layout        jsonb NOT NULL DEFAULT '{}'::jsonb, -- {node_id: {x, y}}
  version       integer NOT NULL DEFAULT 1,       -- optimistic lock, +1 per save
  ir_hash       text NOT NULL,                    -- sha256 of canonical IR
  created_by    text REFERENCES app_user(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workflow_project_idx ON workflow(project_id);

CREATE TABLE workflow_version (
  id            text PRIMARY KEY,                 -- ver_<ulid>
  workflow_id   text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
  semver        text NOT NULL,
  ir            jsonb NOT NULL,
  layout        jsonb NOT NULL,
  ir_hash       text NOT NULL,
  workspace_commit text,                          -- git commit of project workspace
  message       text NOT NULL DEFAULT '',
  created_by    text REFERENCES app_user(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, semver)
);

CREATE TABLE deployment (
  id            text PRIMARY KEY,                 -- dep_<ulid>
  workflow_id   text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
  version_id    text NOT NULL REFERENCES workflow_version(id),
  environment   text NOT NULL DEFAULT 'prod',
  api_key_hash  text NOT NULL,                    -- sha256 of the API key; key shown once
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE thread (
  id            text PRIMARY KEY,                 -- th_<ulid> (== LangGraph thread_id)
  workflow_id   text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
  deployment_id text REFERENCES deployment(id),
  title         text,
  status        text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle','running','interrupted','error')),
  created_by    text REFERENCES app_user(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX thread_workflow_idx ON thread(workflow_id, updated_at DESC);

CREATE TABLE run (
  id            text PRIMARY KEY,                 -- run_<ulid>
  thread_id     text NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
  workflow_version_id text REFERENCES workflow_version(id), -- NULL = draft run
  ir_hash       text NOT NULL,
  mode          text NOT NULL DEFAULT 'normal' CHECK (mode IN ('normal','debug')),
  status        text NOT NULL CHECK (status IN ('running','success','interrupted','error','cancelled')),
  error         text,
  tokens_in     integer NOT NULL DEFAULT 0,
  tokens_out    integer NOT NULL DEFAULT 0,
  cost_usd      numeric(12,6) NOT NULL DEFAULT 0,
  trace_url     text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz
);
CREATE INDEX run_thread_idx ON run(thread_id, started_at DESC);

CREATE TABLE interrupt_task (
  id            text PRIMARY KEY,                 -- LangGraph interrupt id
  thread_id     text NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
  run_id        text NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  canvas_node_id text NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('tool_approval','approval','input')),
  payload       jsonb NOT NULL,
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','decided','expired')),
  assignee_role text,
  decision      jsonb,
  decided_by    text REFERENCES app_user(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz
);
CREATE INDEX interrupt_pending_idx ON interrupt_task(status, created_at) WHERE status = 'pending';

CREATE TABLE secret (
  id            text PRIMARY KEY,                 -- sec_<ulid>
  project_id    text NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name          text NOT NULL,                    -- e.g. OPENAI_API_KEY
  ciphertext    bytea NOT NULL,                   -- Fernet (M5) / KMS envelope (M8)
  created_by    text REFERENCES app_user(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, name)
);

CREATE TABLE connection (
  id            text PRIMARY KEY,                 -- con_<ulid>
  project_id    text NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind          text NOT NULL,                    -- 'model_provider' | 'mcp' | 'http' | 'postgres' ...
  name          text NOT NULL,
  config        jsonb NOT NULL,                   -- non-secret settings; secrets referenced by name
  UNIQUE (project_id, name)
);

CREATE TABLE trigger (
  id            text PRIMARY KEY,                 -- trg_<ulid>
  workflow_id   text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
  deployment_id text REFERENCES deployment(id),
  kind          text NOT NULL CHECK (kind IN ('webhook','cron')),
  config        jsonb NOT NULL,                   -- webhook: {secret_name, input_mapping}; cron: {expr, tz, input}
  enabled       boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  org_id        text REFERENCES organization(id),
  actor_id      text REFERENCES app_user(id),
  action        text NOT NULL,                    -- 'workflow.update', 'run.approve', 'secret.read', ...
  target        text NOT NULL,                    -- id of the affected object
  detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip            inet,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_org_time_idx ON audit_log(org_id, created_at DESC);
