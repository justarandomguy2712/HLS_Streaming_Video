import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PHASE05_PLAYWRIGHT || "playwright");
const [receipt] = process.argv.slice(2);
const origin = process.env.PHASE05_ORIGIN || "http://localhost:8000";
const entries = JSON.parse(await readFile(receipt, "utf8"));
const longer = entries.reduce((first, second) => first.duration > second.duration ? first : second);
const browser = await chromium.launch({
  channel: "msedge", headless: true, args: ["--autoplay-policy=no-user-gesture-required"]
});
try {
  const page = await browser.newPage();
  const requested = [];
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.origin !== origin && response.ok()) requested.push(url.href);
  });
  await page.goto(origin);
  for (const video of entries) {
    const row = page.locator(`#video-library [data-video-id="${video.id}"]`);
    await row.getByRole("button", { name: /^Play / }).waitFor();
    assert.match(await row.innerText(), /ready/);
  }
  await page.locator(`#video-library [data-video-id="${longer.id}"]`)
    .getByRole("button", { name: /^Play / }).click();
  await page.waitForFunction(() => document.querySelector("video").currentTime > 0.2);
  await page.locator("#player-video").evaluate(video => { video.pause(); video.currentTime = 23.5; });
  await page.waitForFunction(() => {
    const video = document.querySelector("video");
    return !video.seeking && video.readyState >= 2 && Math.abs(video.currentTime - 23.5) < 0.2;
  });
  await page.locator("#player-video").evaluate(video => video.play());
  await page.waitForFunction(() => document.querySelector("video").currentTime > 24);
  assert.ok(requested.some(url => url.endsWith(`/${longer.id}/playlist.m3u8`)));
  assert.ok(requested.some(url => url.endsWith(`/${longer.id}/seg_000005.m4s`)));
  console.log(`PASS Edge ${browser.version()}: persistent library, Play, CDN playback and seek to 23.5s`);

  const shorter = entries.reduce((first, second) => first.duration < second.duration ? first : second);
  await page.locator(`#video-library [data-video-id="${shorter.id}"]`)
    .getByRole("button", { name: /^Play / }).click();
  await page.waitForFunction(() => {
    const video = document.querySelector("video");
    return video.duration < 5 && video.currentTime > 0.15 && !video.paused;
  });
  assert.ok(requested.some(url => url.endsWith(`/${shorter.id}/seg_000000.m4s`)));
  console.log("PASS switch to the short video and play its single CDN segment");

  const uploadId = "66666666-6666-4666-8666-666666666666";
  await page.route(`**/api/videos/${uploadId}/status`, route => route.fulfill({
    json: { id: uploadId, status: "ready" }
  }));
  await page.route("**/api/videos/upload", route => {
    assert.equal(route.request().method(), "POST");
    assert.equal(route.request().headers()["content-type"], "video/mp4");
    return route.fulfill({
      status: 202, headers: { Location: `/api/videos/${uploadId}/status` },
      json: { id: uploadId, status: "uploaded" },
    });
  });
  await page.locator("#video").setInputFiles({
    name: "browser-check.mp4", mimeType: "video/mp4", buffer: Buffer.from("mock upload")
  });
  await page.locator("#upload-form").getByRole("button", { name: "Tải lên" }).click();
  await page.waitForFunction(id => document.querySelector("#player-id").value === id, uploadId);
  console.log("PASS upload form calls the library API and receives the Video ID");
} finally {
  await browser.close();
}
