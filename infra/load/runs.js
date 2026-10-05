// Load test (M8-T5): concurrent streaming runs against compose prod with demo models (infra/load/compose.load.yml).
//   k6 run infra/load/runs.js                      # 200 concurrent streams for 2 minutes through nginx
//   k6 run -e VUS=50 -e HOLD=30s -e BASE_URL=http://localhost:8080/api infra/load/runs.js
//
// Each virtual user streams one run after another, so VUS is the number of runs in flight. k6 reads a stream's
// body only when it ends, so "first event" is the time until the response starts (RUN_STARTED is its first
// chunk) and "run" is the whole stream; the API's own view is in Grafana (agentcanvas_run_duration_seconds).
import http from "k6/http";
import { check, fail } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8080/api";
const VUS = Number(__ENV.VUS || 200);
const HOLD = __ENV.HOLD || "2m";
const JSON_HEADERS = { "Content-Type": "application/json" };

const firstEvent = new Trend("first_event_ms", true);
const runTime = new Trend("run_ms", true);
const finished = new Rate("runs_finished");

export const options = {
  scenarios: {
    streams: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: VUS },
        { duration: HOLD, target: VUS },
        { duration: "10s", target: 0 },
      ],
      gracefulRampDown: "60s",
    },
  },
  thresholds: {
    first_event_ms: ["p(95)<250"],
    runs_finished: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function setup() {
  const res = http.post(
    `${BASE}/v1/projects/prj_default/workflows`,
    JSON.stringify({ name: `Load test ${Date.now()}`, template: "support_triage" }),
    { headers: JSON_HEADERS },
  );
  if (res.status !== 201 && res.status !== 200) fail(`could not create the workflow: ${res.status} ${res.body}`);
  return { wf: res.json("id") };
}

export default function (data) {
  const res = http.post(
    `${BASE}/v1/workflows/${data.wf}/runs/stream`,
    JSON.stringify({ input: { ticket: `Invoice question ${__VU}-${__ITER}` } }),
    { headers: JSON_HEADERS, timeout: "120s", tags: { name: "runs/stream" } },
  );
  firstEvent.add(res.timings.waiting);
  runTime.add(res.timings.duration);
  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "run finished": (r) => typeof r.body === "string" && r.body.includes('"RUN_FINISHED"'),
  });
  finished.add(ok);
}
