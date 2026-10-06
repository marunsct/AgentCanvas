// Load test of a published deployment (S13 exit): does online scoring add latency to deployment responses?
// Against compose prod with demo models (infra/load/compose.load.yml). Run it twice and compare p95:
//   k6 run -e ONLINE=0 infra/load/deployment.js     # no online evaluator
//   k6 run -e ONLINE=1 infra/load/deployment.js     # every run scored after its response (sampling 100 %)
// nginx limits the public deployment API per client address, so point k6 at the API on the compose network:
//   docker run --rm --network agentcanvas_default -v "$PWD/infra/load:/load" grafana/k6 run \
//     -e BASE_URL=http://api:8000 -e ONLINE=1 /load/deployment.js
// Each virtual user calls `runs/wait` one run after another, so VUS is the number of requests in flight; with
// -e RATE=<n> requests arrive at a fixed rate instead, which measures latency below saturation.
import http from "k6/http";
import { check, fail } from "k6";
import { Rate } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8080/api";
const VUS = Number(__ENV.VUS || 100);
const HOLD = __ENV.HOLD || "1m";
const ONLINE = __ENV.ONLINE === "1";
const RATE = Number(__ENV.RATE || 0); // requests/s at a fixed arrival rate; 0: VUS users back to back (saturates)
const JSON_HEADERS = { "Content-Type": "application/json" };
const answered = new Rate("runs_succeeded");

const calls = RATE
  ? { executor: "constant-arrival-rate", rate: RATE, timeUnit: "1s", duration: HOLD, preAllocatedVUs: VUS }
  : {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "15s", target: VUS },
        { duration: HOLD, target: VUS },
        { duration: "5s", target: 0 },
      ],
      gracefulRampDown: "30s",
    };

export const options = {
  scenarios: { calls },
  thresholds: { runs_succeeded: ["rate>0.99"], http_req_failed: ["rate<0.01"] },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function ok(res, what) {
  if (res.status < 200 || res.status > 299) fail(`${what}: ${res.status} ${res.body}`);
  return res.json();
}

export function setup() {
  const wf = ok(
    http.post(`${BASE}/v1/projects/prj_default/workflows`, JSON.stringify({ name: `Deployment load ${Date.now()}` }),
      { headers: JSON_HEADERS }),
    "create workflow",
  );
  const ir = {
    id: wf.id, name: "Support agent",
    state: { channels: [{ name: "question" }, { name: "answer" }] },
    resources: [{ id: "r_m", type: "model.chat", name: "model", config: { model: "anthropic:claude-haiku-4-5" } }],
    nodes: [{ id: "n_a", type: "core.llm", name: "answer", config: { prompt: "{{ state.question }}", output: "answer" } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_a" } },
      { id: "e2", kind: "flow", source: { node: "n_a" }, target: { node: "END" } },
      { id: "w1", kind: "wiring", source: { node: "r_m" }, target: { node: "n_a", port: "model" } },
    ],
  };
  ok(http.put(`${BASE}/v1/workflows/${wf.id}`, JSON.stringify({ ir }),
    { headers: { ...JSON_HEADERS, "If-Match": String(wf.version) } }), "save workflow");
  const pub = ok(http.post(`${BASE}/v1/workflows/${wf.id}/publish`, "{}", { headers: JSON_HEADERS }), "publish");
  if (ONLINE) {
    const ev = ok(http.post(`${BASE}/v1/workflows/${wf.id}/evaluators`, JSON.stringify({
      name: "on topic", kind: "regex", config: { channel: "answer", pattern: "^(?!.*\\bjoke\\b)" },
    }), { headers: JSON_HEADERS }), "evaluator");
    ok(http.post(`${BASE}/v1/deployments/${pub.deployment.id}/online-evaluators`, JSON.stringify({
      evaluator_id: ev.id, sampling: 1.0, alert_below: 0.5,
    }), { headers: JSON_HEADERS }), "online evaluator");
  }
  return { dep: pub.deployment.id, key: pub.api_key };
}

export default function (data) {
  const res = http.post(
    `${BASE}/v1/deployments/${data.dep}/runs/wait`,
    JSON.stringify({ input: { question: `where is order ${__VU}-${__ITER}?` }, metadata: { tier: "load" } }),
    { headers: { ...JSON_HEADERS, Authorization: `Bearer ${data.key}` }, timeout: "60s", tags: { name: "runs/wait" } },
  );
  answered.add(check(res, { "status 200": (r) => r.status === 200 && r.json("status") === "success" }));
}
