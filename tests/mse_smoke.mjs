import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join } from "node:path";
import { parsePlaylist, segmentIndexAt } from "../frontend/js/playlist-parser.js";
import { readLabBytes, readLabHeader } from "../admin/js/binary-lab.js";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PHASE05_PLAYWRIGHT || "playwright");
const origin = process.env.PHASE05_ORIGIN || "http://localhost:8000";
const realId = process.env.PHASE05_VIDEO_ID || "ee3e7935-4b66-492d-a7f5-f937fb4be8f1";
const fakeId = "33333333-3333-4333-8333-333333333333";
const [directory, codecs] = process.argv.slice(2);
if (!directory || !codecs) throw new Error("Usage: node tests/mse_smoke.mjs <fMP4 fixture directory> <codec string>");

const video = page => page.locator("#player-video");
const waitVideo = (page, predicate, arg) => page.waitForFunction(predicate, arg, { timeout: 20_000 });

async function open(page, id) {
  await page.goto(`${origin}/?video=${id}`);
  await page.locator("#player-status").getByText("Đã nạp phân đoạn đầu.", { exact: false }).waitFor();
  await waitVideo(page, () => document.querySelector("video").readyState >= 2);
}

async function testLabAndPlaylist() {
  const manifest = await readFile(join(directory, "playlist.m3u8"), "utf8");
  const timeline = parsePlaylist(manifest, `${origin}/fixture/playlist.m3u8`);
  assert.equal(timeline.duration, 48);
  assert.equal(timeline.segments.length, 12);
  assert.equal(segmentIndexAt(timeline.segments, 29.5), 7);
  assert.equal(segmentIndexAt(timeline.segments, 48), 11);
  assert.throws(() => parsePlaylist(manifest.replace("#EXT-X-ENDLIST", ""), origin), /chưa hoàn chỉnh/);
  assert.throws(() => parsePlaylist(manifest.replace('URI="init.mp4"', 'URI="data:x"'), origin), /URI/);

  const lab = new Uint8Array(55);
  lab.set([0x48, 0x4c, 0x53, 0x4c, 0x41, 0x42, 0x1a, 0x0a], 0);
  new DataView(lab.buffer).setUint16(8, 1);
  new DataView(lab.buffer).setBigUint64(12, 3n);
  lab.set([0, 128, 255], 52);
  const blob = new Blob([lab]);
  assert.equal((await readLabHeader(blob)).payloadLength, 3n);
  assert.deepEqual(await readLabBytes(blob, 53, 2), Uint8Array.of(128, 255));
  await assert.rejects(readLabBytes(blob, 53, 3), RangeError);
  await assert.rejects(readLabHeader(blob.slice(0, 54)), /độ dài/);
  console.log("PASS playlist timeline, malformed/unsupported cases, HLSLAB header and byte slice");
}

async function testRealCdn(browser) {
  const page = await browser.newPage();
  try {
    const responses = new Map();
    page.on("response", response => {
      if (response.url().endsWith(".m4s") || response.url().endsWith(".m3u8")) {
        responses.set(response.url(), response);
      }
    });
    await open(page, realId);
    assert.deepEqual([...responses.keys()].filter(url => url.endsWith(".m4s")).map(url => url.split("/").at(-1)),
      ["seg_000000.m4s"]);
    const resource = [...responses.values()].find(response => response.url().endsWith("seg_000000.m4s"));
    assert.notEqual(new URL(resource.url()).origin, origin);
    assert.equal(resource.status(), 200);
    assert.equal(await resource.headerValue("access-control-allow-origin"), origin);
    await video(page).evaluate(element => element.play());
    await waitVideo(page, () => document.querySelector("video").currentTime >= 0.35);
    const pausedAt = await video(page).evaluate(element => { element.pause(); return element.currentTime; });
    assert.ok(Math.abs((await video(page).evaluate(element => element.currentTime)) - pausedAt) < 0.10);
    await video(page).evaluate(element => { element.currentTime = 8.25; });
    await waitVideo(page, () => {
      const element = document.querySelector("video");
      return !element.seeking && Math.abs(element.currentTime - 8.25) < 0.2;
    });
    await video(page).evaluate(element => element.play());
    await waitVideo(page, () => document.querySelector("video").ended);
    assert.ok(await video(page).evaluate(element => element.getVideoPlaybackQuality().totalVideoFrames > 0));
    await video(page).evaluate(element => element.play());
    await waitVideo(page, () => {
      const element = document.querySelector("video");
      return element.currentTime > 0.15 && element.currentTime < 3 && !element.paused;
    });
    console.log(`PASS Edge ${browser.version()}: real CDN, startup, play, pause, seek, end, native replay`);
  } finally {
    await page.close();
  }
}

async function mountFixture(page, failTarget) {
  const requests = [];
  await page.route(`**/api/videos/${fakeId}/playback`, route => route.fulfill({
    json: { id: fakeId, playback_url: "/fixture/playlist.m3u8", codecs,
      forward_buffer_seconds: 12, back_buffer_seconds: 12 }
  }));
  await page.route("**/fixture/*", async route => {
    const name = new URL(route.request().url()).pathname.split("/").at(-1);
    assert.match(name, /^(playlist\.m3u8|init\.mp4|seg_\d{6}\.m4s)$/);
    requests.push(name);
    if (failTarget(name)) return route.fulfill({ status: 503, body: "Injected test failure" });
    return route.fulfill({ path: join(directory, name) });
  });
  return requests;
}

async function testLongSeekAndRetries(browser) {
  const page = await browser.newPage();
  try {
    let injected = 0;
    const requests = await mountFixture(page, name => name === "seg_000007.m4s" && injected++ === 0);
    await open(page, fakeId);
    assert.deepEqual(requests, ["playlist.m3u8", "init.mp4", "seg_000000.m4s"]);
    await video(page).evaluate(element => { element.currentTime = 29.5; });
    await waitVideo(page, () => {
      const element = document.querySelector("video");
      return !element.seeking && Math.abs(element.currentTime - 29.5) < 0.2 && element.readyState >= 2;
    });
    assert.deepEqual(requests.slice(3), ["seg_000007.m4s", "seg_000007.m4s"]);
    await video(page).evaluate(element => element.play());
    await waitVideo(page, () => document.querySelector("video").currentTime > 29.8);
    console.log("PASS 48-second VOD: distant paused seek, no intermediate downloads, transient 503 retried");
  } finally {
    await page.close();
  }
}

async function testRollingBuffer(browser) {
  const page = await browser.newPage();
  try {
    const requests = await mountFixture(page, () => false);
    await open(page, fakeId);
    await video(page).evaluate(element => { element.playbackRate = 4; return element.play(); });
    await waitVideo(page, () => document.querySelector("video").currentTime >= 21);
    const buffer = await video(page).evaluate(element => ({
      start: element.buffered.start(0), end: element.buffered.end(element.buffered.length - 1),
      time: element.currentTime
    }));
    assert.ok(buffer.start >= 4, JSON.stringify(buffer));
    assert.ok(buffer.end - buffer.time <= 16.2, JSON.stringify(buffer));
    assert.ok(!requests.includes("seg_000011.m4s"), requests);
    await video(page).evaluate(element => { element.currentTime = 0.4; });
    await waitVideo(page, () => {
      const element = document.querySelector("video");
      return !element.seeking && element.currentTime >= 0.4 && element.currentTime < 4;
    });
    assert.equal(requests.filter(name => name === "seg_000000.m4s").length, 2);
    await video(page).evaluate(element => { element.currentTime = 46; });
    await waitVideo(page, () => document.querySelector("video").ended);
    assert.ok(await video(page).evaluate(element => element.buffered.start(0) > 35));
    await video(page).evaluate(element => element.play());
    await waitVideo(page, () => {
      const element = document.querySelector("video");
      return !element.seeking && element.currentTime > 0.15 && element.currentTime < 3;
    });
    assert.equal(requests.filter(name => name === "seg_000000.m4s").length, 3);
    console.log("PASS bounded rolling buffer, backward seek and replay after the beginning was evicted");
  } finally {
    await page.close();
  }
}

async function testTerminalError(browser) {
  const page = await browser.newPage();
  try {
    let offline = true;
    const requests = await mountFixture(page, name => offline && name === "seg_000000.m4s");
    await page.goto(`${origin}/?video=${fakeId}`);
    await page.locator("#player-status").getByText("HTTP 503", { exact: false }).waitFor();
    assert.equal(requests.filter(name => name === "seg_000000.m4s").length, 3);
    offline = false;
    await page.getByRole("button", { name: "Mở video" }).click();
    await page.locator("#player-status").getByText("Đã nạp phân đoạn đầu.", { exact: false }).waitFor();
    await video(page).evaluate(element => element.play());
    await waitVideo(page, () => document.querySelector("video").currentTime > 0.2);
    console.log("PASS terminal network error shown after retries and explicit reopen recovers playback");
  } finally {
    await page.close();
  }
}

await testLabAndPlaylist();
const browser = await chromium.launch({
  channel: "msedge", headless: true, args: ["--autoplay-policy=no-user-gesture-required"]
});
try {
  await testRealCdn(browser);
  await testLongSeekAndRetries(browser);
  await testRollingBuffer(browser);
  await testTerminalError(browser);
} finally {
  await browser.close();
}
