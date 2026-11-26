import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PHASE05_PLAYWRIGHT || "playwright");
const [website, worker, source] = process.argv.slice(2);
const local = "http://127.0.0.1:8765";
const publicPorts = [website, worker].map(url => `${new URL(url).host}=public`);
const browser = await chromium.launch({ channel: "msedge", headless: true,
  args: [`--ip-address-space-overrides=${publicPorts.join(",")}`] });

async function login(page, url) {
  await page.goto(url);
  await page.locator("#username").fill("lab-admin");
  await page.locator("#password").fill("fixture-password");
  await page.getByRole("button", {name: "Đăng nhập"}).click();
  await page.locator("#companion-connect").waitFor();
}

try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const snapshots = [];
  page.on("response", async response => {
    if (response.url() === local + "/api/companion/jobs" && response.request().method() === "GET" && response.ok()) {
      try { snapshots.push(...(await response.json()).jobs); } catch { /* Navigation may abort polling. */ }
    }
  });
  await login(page, website);
  // Cấp quyền cho origin kiểm thử như người dùng bấm Allow đã cấp quyền.
  await context.grantPermissions(["local-network-access"], {origin: website});
  await page.locator("#companion-connect").click();
  await page.locator("#companion-upload").waitFor();
  await page.locator("#companion-file").setInputFiles(source);
  await page.locator("#companion-title").fill("Video trình duyệt");
  const uploading = page.waitForResponse(response =>
    response.url() === local + "/api/companion/jobs" && response.request().method() === "POST", {timeout: 180_000});
  await page.locator("#companion-submit").click();
  assert.equal((await uploading).status(), 202);
  await page.locator("#companion-transfer progress[value='100']").waitFor({state: "attached"});
  await login(page, website);
  await page.locator("#companion-connect").click();
  const card = page.locator("#companion-jobs article").filter({hasText: "Video trình duyệt"});
  await card.getByText("Xử lý thất bại", {exact: false}).waitFor({timeout: 180_000});
  assert.equal(await page.getByRole("button", {name: "Phát Video trình duyệt", exact: true}).count(), 0);
  await card.getByText("Đã xác minh SHA-256", {exact: false}).waitFor();
  await card.getByRole("button", {name: "Thử lại"}).click();
  await card.getByText("Video đã sẵn sàng", {exact: false}).waitFor();
  await page.getByRole("button", {name: "Phát Video trình duyệt", exact: true}).waitFor();
  await card.getByText("Đã dọn tệp tạm", {exact: false}).waitFor();
  const failed = snapshots.findLast(job => job.phase === "FAILED");
  assert.ok(failed?.ffmpeg_done && failed.segments_uploaded === failed.segments_created);
  assert.ok(failed.media_seconds > 0 && failed.duration > 0 && failed.speed > 0);
  assert.ok(failed.total_bytes > 0 && failed.total_bytes === failed.transfer_bytes);
  assert.ok(failed.total_bytes === failed.uploaded_bytes && failed.total_bytes === failed.verified_bytes &&
    failed.bytes_per_second > 0);
  const localPage = await context.newPage();
  await login(localPage, local);
  await localPage.locator("#companion-upload").waitFor();
  await localPage.locator("#companion-jobs").getByText("Video trình duyệt", {exact: true}).waitFor();
  console.log("PASS Edge cross-origin LNA, progress counters, refresh, retry, library update and loopback UI.");
} finally {
  await browser.close();
}
