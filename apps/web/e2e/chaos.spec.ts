import { expect, test } from "./fixtures";

// When the API misbehaves the app says so: no page goes blank, none throws an uncaught error, none crashes into the
// error boundary. The failures are the ones a deployment really produces: the server's own 500, a proxy's HTML error
// page (502), the single-page fallback answering an API path (200 with HTML), a dropped connection, and an API that
// answers with JSON of another shape than the app expects. Of these the last is a page and a server of different
// versions: there the error boundary (with its Reload hint) is the safety net, and only a blank page or an uncaught
// error counts. In every other case the page itself says what failed.

const MODES: Record<string, (route: import("@playwright/test").Route) => Promise<void>> = {
  "server error": (r) => r.fulfill({ status: 500, contentType: "application/problem+json", body: '{"title":"Internal error","detail":"boom","status":500}' }),
  "proxy error page": (r) => r.fulfill({ status: 502, contentType: "text/html", body: "<html><body>502 Bad Gateway</body></html>" }),
  "html where json was expected": (r) => r.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html><body>app</body></html>" }),
  "dropped connection": (r) => r.abort("connectionrefused"),
  "unexpected object": (r) => r.fulfill({ status: 200, contentType: "application/json", body: '{"unexpected":true}' }),
  "null": (r) => r.fulfill({ status: 200, contentType: "application/json", body: "null" }),
};

const PAGES = (wf: string) => [
  "/", `/workflows/${wf}`, `/workflows/${wf}/evaluation`, `/workflows/${wf}/workspace`, "/setup", "/playground", "/inbox",
  "/memory", "/knowledge", "/workspace", "/events", "/members", "/audit", "/policies",
];

const BOUNDARY_IS_FINE = new Set(["unexpected object"]);

for (const [mode, handler] of Object.entries(MODES)) {
  test(`every page survives: ${mode}`, async ({ page, request }) => {
    test.setTimeout(240_000);
    const created = await request.post("http://localhost:5173/api/v1/projects/prj_default/workflows", { data: { name: "Chaos" } });
    const wf = (await created.json()).id as string;
    const problems: string[] = [];
    let where = "";
    page.on("pageerror", (e) => problems.push(`${where}: uncaught ${e.message.slice(0, 120)}`));
    // sign-in, "who am I" and the project list (the start-up calls) are answered, so the pages themselves mount and have to cope with what they ask for
    await page.route(/\/api\/v1\/(?!auth\/config|me(\/|\?|$)|projects(\?|$)).*/, handler);
    for (const path of PAGES(wf)) {
      where = path;
      await page.goto(path);
      await page.waitForTimeout(1200);
      const text = (await page.locator("body").innerText()).trim();
      if (text.length < 5) problems.push(`${path}: blank`);
      if (process.env.CHAOS_SHOW) console.log(`SHOW ${mode} ${path}: ${text.replace(/\s+/g, " ").slice(0, 150)}`);  // debugging aid
      if (!BOUNDARY_IS_FINE.has(mode) && (await page.getByText(/crashed:/).count())) problems.push(`${path}: crashed into the error boundary: ${(await page.getByText(/crashed:/).first().innerText()).slice(0, 120)}`);
    }
    console.log(`CHAOS ${mode}: ${problems.length} problems\n${problems.join("\n")}`);
    expect(problems, mode).toEqual([]);
  });
}
