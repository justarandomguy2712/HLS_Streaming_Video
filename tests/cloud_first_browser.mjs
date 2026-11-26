import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PHASE05_PLAYWRIGHT || "playwright");
const [receipt] = process.argv.slice(2);
const origin = process.env.PHASE05_ORIGIN || "http://localhost:8000";
const records = (await readFile(receipt, "utf8")).trim().split(/\r?\n/).map(JSON.parse)
  .filter(record => record.mode === "streaming");
const longest = records.reduce((a, b) => a.duration > b.duration ? a : b);
const short = records.find(record => record.case === "TC02");
assert.ok(short && longest, "Cần receipt có TC02 và video dài đã xuất bản.");
const video = page => page.locator("#player-video");
const browser = await chromium.launch({
  channel: "msedge", headless: true, args: ["--autoplay-policy=no-user-gesture-required"]
});

try {
  const page = await browser.newPage();
  const segments = [];
  let playbackCalls = 0;
  page.on("request", req => {
    const path = new URL(req.url()).pathname;
    const match = path.match(/\/seg_(\d+)\.m4s$/);
    if (match) segments.push(Number(match[1]));
    if (path.endsWith("/playback")) playbackCalls++;
  });
  await page.addInitScript(() => {
    const append = SourceBuffer.prototype.appendBuffer;
    window.quotaFailures = 0;
    SourceBuffer.prototype.appendBuffer = function(buffer) {
      const offset = new Uint8Array(buffer, 4, 4);
      const type = String.fromCharCode(...offset);
      if (window.injectQuota && type !== "ftyp") {
        window.injectQuota = false;
        window.quotaFailures++;
        throw new DOMException("quota controlled test", "QuotaExceededError");
      }
      return append.call(this, buffer);
    };
  });

  async function open(record) {
    const start = performance.now();
    await page.goto(origin + "/?video=" + record.id);
    await page.waitForFunction(() => {
      const player = document.querySelector("video");
      return player.readyState >= 2 && player.buffered.length > 0;
    }, undefined, { timeout: 25_000 });
    return Math.round(performance.now() - start);
  }

  async function seek(target) {
    const start = performance.now();
    const firstRequest = segments.length;
    await video(page).evaluate((player, time) => { player.currentTime = time; }, target);
    await page.waitForFunction(time => {
      const player = document.querySelector("video");
      return !player.seeking && Math.abs(player.currentTime - time) < 0.6 && player.readyState >= 2;
    }, target, { timeout: 25_000 });
    return { target, ms: Math.round(performance.now() - start), downloaded: segments.slice(firstRequest) };
  }

  const startupMs = await open(longest);
  assert.ok(segments.length < 5);
  const targets = longest.duration >= 10800
    ? [7200, longest.duration / 2, longest.duration - 5, 1] : [longest.duration / 2, longest.duration - 2, 1];
  const seeks = [];
  for (const target of targets) {
    const result = await seek(target);
    assert.ok(result.downloaded.length <= 3);
    if (longest.duration >= 10800 && target === 7200) {
      assert.ok(result.downloaded.includes(119) || result.downloaded.includes(120), result);
    }
    seeks.push(result);
  }
  console.log(JSON.stringify({ video: longest.id, duration: longest.duration, startupMs, seeks }));

  if (longest.duration >= 10800) {
    const start = performance.now();
    await video(page).evaluate(async player => { player.playbackRate = 4; await player.play(); });
    await page.waitForFunction(() => document.querySelector("video").currentTime >= 150,
      undefined, { timeout: 65_000 });
    assert.equal(await video(page).evaluate(player => player.error), null);
    console.log(JSON.stringify({ continuousMediaSeconds: 149, wallMs: Math.round(performance.now() - start) }));
  }

  let expired = 0;
  await page.route("**/seg_000001.m4s", async route => {
    if (expired++ === 0) {
      await route.fulfill({ status: 403, headers: { "access-control-allow-origin": origin } });
    } else await route.continue();
  });
  await open(short);
  const beforeRefresh = playbackCalls;
  await page.evaluate(() => { window.injectQuota = true; });
  await video(page).evaluate(async player => { player.playbackRate = 4; await player.play(); });
  await page.waitForFunction(() => {
    const player = document.querySelector("video");
    return player.currentTime >= 24 && window.quotaFailures === 1;
  }, undefined, { timeout: 25_000 });
  assert.ok(playbackCalls > beforeRefresh && expired >= 2);

  await video(page).evaluate(player => player.pause());
  const oldUrl = await video(page).getAttribute("src");
  await page.locator("#player-id").fill(longest.id);
  await page.locator("#player-form button").click();
  await page.waitForFunction(url => {
    const player = document.querySelector("video");
    return player.src !== url && player.readyState >= 2;
  }, oldUrl, { timeout: 25_000 });
  assert.equal(await page.evaluate(async url => {
    try { await fetch(url); return false; } catch { return true; }
  }, oldUrl), true);
  console.log("PASS uninterrupted advance to 24s, expired-URL refresh, quota injection and player switch");
} finally {
  await browser.close();
}
