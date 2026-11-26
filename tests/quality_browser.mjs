import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PHASE05_PLAYWRIGHT || "playwright");
const origin = process.env.PHASE05_ORIGIN || "http://localhost:8000";
const [receipt, ...cases] = process.argv.slice(2);
const browser = await chromium.launch({
  channel: "msedge", headless: true, args: ["--autoplay-policy=no-user-gesture-required"]
});
try {
  const page = await browser.newPage();
  if (receipt === "--catalog") {
    await page.goto(origin);
    const rows = page.locator("#video-library li");
    await page.waitForFunction(() => document.querySelectorAll("#video-library li").length === 20);
    const next = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === "/api/videos" && url.searchParams.has("cursor") && response.ok();
    });
    await page.locator("#library-more").click();
    await next;
    await page.waitForFunction(() => document.querySelectorAll("#video-library li").length === 40);
    const ids = await rows.evaluateAll(nodes => nodes.map(node => node.dataset.videoId));
    assert.equal(new Set(ids).size, 40);
    console.log("PASS browser lazy loading with cursor and 40 distinct rows after click");
  } else {
    const entries = (await readFile(receipt, "utf8")).trim().split(/\r?\n/).map(JSON.parse);
    const video = page.locator("#player-video");
    for (const name of cases) {
      const record = entries.find(entry => entry.case === name);
      assert.ok(record, name);
      const segments = [];
      const capture = response => {
        const url = new URL(response.url());
        if (url.origin === new URL(record.url).origin && url.pathname.endsWith(".m4s") && response.ok()) {
          segments.push(url.pathname.split("/").at(-1));
        }
      };
      page.on("response", capture);
      await page.goto(origin + "/?video=" + record.id);
      await page.waitForFunction(() => document.querySelector("video").readyState >= 2,
        undefined, { timeout: 35_000 });
      assert.ok(segments.length > 0);
      if (record.duration > 10000) {
        segments.length = 0;
        await video.evaluate(element => { element.currentTime = 7200; });
        await page.waitForFunction(() => {
          const element = document.querySelector("video");
          return !element.seeking && element.readyState >= 2 && Math.abs(element.currentTime - 7200) < 0.5;
        }, undefined, { timeout: 25_000 });
        assert.ok(segments.some(name => /seg_0001(19|20)\.m4s/.test(name)));
        assert.ok(segments.length <= 3, segments);
        console.log(JSON.stringify({ name, duration: record.duration, seek: 7200, segments }));
      } else {
        const [width, height] = record.resolution.split("x").map(Number);
        await video.evaluate(player => player.play());
        await page.waitForFunction(() => document.querySelector("video").ended,
          undefined, { timeout: 35_000 });
        const decoded = await video.evaluate(player => {
          const quality = player.getVideoPlaybackQuality();
          return { width: player.videoWidth, height: player.videoHeight,
            total: quality.totalVideoFrames, dropped: quality.droppedVideoFrames };
        });
        assert.deepEqual([decoded.width, decoded.height], [width, height]);
        assert.ok(decoded.total > decoded.dropped);
        console.log(JSON.stringify({ name, ...decoded, segments }));
      }
      page.off("response", capture);
    }
  }
} finally {
  await browser.close();
}
