import { chromium } from "playwright";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
const page = await browser.newPage({ viewport: { width: 1300, height: 800 } });
page.on("console", (m) => m.type() === "error" && console.log("console error:", m.text()));
await page.goto("http://localhost:5173");
await page.getByText("Support triage").waitFor();
await page.getByRole("button", { name: "▶ Run" }).click();
await page.getByText("Approval needed").waitFor({ timeout: 10000 });
await page.screenshot({ path: "/tmp/e2e-paused.png" });
await page.getByRole("button", { name: "Approve" }).click();
await page.waitForFunction(() => document.body.innerText.includes("Reply sent."), null, { timeout: 10000 });
await page.screenshot({ path: "/tmp/e2e-done.png" });
// second run on a new thread must work too
await page.getByRole("button", { name: "▶ Run" }).click();
await page.getByText("Approval needed").waitFor({ timeout: 10000 });
await page.getByRole("button", { name: "Reject" }).click();
await page.waitForFunction(() => !document.body.innerText.includes("Approval needed"), null, { timeout: 10000 });
console.log("E2E OK");
await browser.close();
