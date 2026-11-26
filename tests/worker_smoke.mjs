import assert from "node:assert/strict";
import { randomBytes, randomUUID, scryptSync } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { join, relative, resolve } from "node:path";

const require = createRequire(import.meta.url);
const { Miniflare, convertV4MiniflareOptions } = require(process.env.PHASE08_MINIFLARE || "miniflare");
const { chromium } = require(process.env.PHASE05_PLAYWRIGHT || "playwright");
const receipt = JSON.parse(await readFile(process.argv[2], "utf8"));
const adminPassword = randomBytes(18).toString("hex");
const userPassword = randomBytes(18).toString("hex");
const serviceKey = randomBytes(32).toString("hex");
const site = resolve("frontend");
let workerUrl;

const website = createServer(async (request, response) => {
  try {
    const path = new URL(request.url, "http://localhost").pathname;
    if (path === "/js/config.js") {
      response.writeHead(200, {"Content-Type": "text/javascript"});
      response.end(`export const WORKER_ENDPOINT = ${JSON.stringify(workerUrl)};`);
      return;
    }
    const root = path === "/";
    if (!root && !/^\/js\/[a-z-]+\.js$/.test(path)) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, {"Content-Type": root ? "text/html; charset=utf-8" : "text/javascript"});
    response.end(await readFile(join(site, root ? "index.html" : path)));
  } catch {
    response.end();
  }
});
await new Promise(done => website.listen(0, "127.0.0.1", done));
const siteUrl = `http://127.0.0.1:${website.address().port}`;

async function seed(bucket, root, path) {
  for (const entry of await readdir(path, {withFileTypes: true})) {
    const target = join(path, entry.name);
    if (entry.isDirectory()) await seed(bucket, root, target);
    else await bucket.put(relative(root, target).replaceAll("\\", "/"), await readFile(target));
  }
}

const modules = process.env.PHASE08_WORKER_BUNDLE
  ? [{type: "ESModule", path: resolve("cloudflare/worker.js"),
      contents: await readFile(process.env.PHASE08_WORKER_BUNDLE, "utf8")}]
  : await Promise.all(["worker.js", "auth.js", "catalog.js", "http.js"].map(async name => {
    const path = resolve("cloudflare", name);
    return {type: "ESModule", path, contents: await readFile(path, "utf8")};
  }));
const options = {
  modules, modulesRoot: resolve("cloudflare"),
  host: "127.0.0.1", port: 0, compatibilityDate: "2026-08-06", compatibilityFlags: ["nodejs_compat"],
  r2Buckets: ["VIDEOS"], d1Databases: ["DB"], bindings: {
    PROCESSING_SERVICE_TOKEN: serviceKey, ALLOWED_ORIGINS: siteUrl, SESSION_TTL_SECONDS: "600",
  },
};
const worker = new Miniflare(convertV4MiniflareOptions?.(options) ?? options);
try {
  workerUrl = (await worker.ready).origin;
  const bucket = await worker.getR2Bucket("VIDEOS");
  const db = await worker.getD1Database("DB");
  const schema = await readFile("cloudflare/migrations/0001_unified.sql", "utf8");
  await db.batch(schema.split(";").map(line => line.trim()).filter(Boolean).map(sql => db.prepare(sql)));
  const salt = randomBytes(16);
  const adminHash = scryptSync(adminPassword, salt, 32, {N: 16384, r: 8, p: 5, maxmem: 64 * 1024 * 1024});
  await db.prepare("INSERT INTO users(id,username,password_hash,role) VALUES(?,?,?,'ADMIN')")
    .bind(randomUUID(), "lab-admin", ["scrypt", salt.toString("hex"), adminHash.toString("hex")].join("$")).run();
  await seed(bucket, receipt.r2, join(receipt.r2, "videos"));
  const oldIndex = receipt.payloads ? null : JSON.parse(await readFile(join(receipt.r2, "library.json"), "utf8"));

  const call = (path, {method = "GET", token, body, headers = {}} = {}) =>
    fetch(workerUrl + path, {
      method,
      headers: {Origin: siteUrl, ...(token ? {Authorization: "Bearer " + token} : {}),
        ...(body === undefined ? {} : {"Content-Type": "application/json"}), ...headers},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  const login = (name, password) => call("/api/auth/login", {method: "POST", body: {username: name, password}});
  const adminLogin = await login("lab-admin", adminPassword);
  assert.equal(adminLogin.status, 200);
  const admin = (await adminLogin.json()).token;
  const created = await call("/api/admin/users", {method: "POST", token: admin,
    body: {username: "lab-user", password: userPassword, role: "ADMIN"}});
  assert.equal(created.status, 201);
  const userRow = await created.json();
  assert.equal(userRow.role, "USER");
  assert.equal(userRow.password_hash, undefined);
  const userLogin = await login("lab-user", userPassword);
  assert.equal(userLogin.status, 200);
  const user = (await userLogin.json()).token;
  const base = "/api/videos/" + receipt.id;
  assert.equal((await call(base + "/media/init.mp4")).status, 401);
  assert.equal((await call("/api/admin/videos/" + receipt.id, {method: "DELETE", token: user})).status, 403);
  assert.equal((await call("/api/admin/users", {token: user})).status, 403);
  assert.equal((await call("/api/local/videos", {method: "POST", token: user, body: {}})).status, 401);
  assert.equal((await call("/api/upload", {method: "POST", token: user})).status, 405);

  for (const id of [receipt.id, receipt.other_id]) {
    let body = receipt.payloads?.find(item => item.id === id);
    if (!body) {
      const entry = oldIndex.items.find(item => item.id === id);
      const prefix = entry.playlist_key.replace(/playlist\.m3u8$/, "");
      const manifest = await (await bucket.get(prefix + "metadata.json")).json();
      const objects = await bucket.list({prefix});
      body = {...entry, original_filename: entry.title + ".mp4", original_size: manifest.source.size_bytes,
        processed_size: objects.objects.reduce((sum, object) => sum + object.size, 0),
        resolution: manifest.source.video.width + "x" + manifest.source.video.height,
        frame_rate: manifest.source.video.avg_frame_rate || "20/1"};
    }
    const result = await call("/api/local/videos", {method: "POST", token: serviceKey, body});
    if (result.status !== 201) throw new Error("Publication failed: " + result.status + " " + await result.text());
    assert.equal((await call("/api/local/videos", {method: "POST", token: serviceKey, body})).status, 200);
  }

  const sessionData = await (await call("/api/auth/me", {token: user})).json();
  assert.equal(sessionData.role, "USER");
  const preflight = await call("/api/auth/login", {method: "OPTIONS"});
  assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), siteUrl);
  assert.match(preflight.headers.get("Access-Control-Allow-Headers"), /Content-Type/);
  assert.equal((await call("/api/videos", {token: user, headers: {Origin: "https://outside.example"}})).status, 403);
  const items = await (await call("/api/videos", {token: user})).json();
  assert.equal(items.items.length, 2);
  const detail = await (await call(base, {token: user})).json();
  assert.match(detail.codecs, /^avc1\./);
  const alias = await call(base + "/playlist", {token: user});
  assert.equal(alias.headers.get("Content-Location"), detail.playback_url);
  assert.match(await alias.text(), /#EXT-X-ENDLIST/);

  const path = base + "/media/seg_000000.m4s";
  const whole = await call(path, {token: user});
  assert.equal(whole.status, 200);
  assert.equal(whole.headers.get("Cache-Control"), "private, no-store");
  const bytes = new Uint8Array(await whole.arrayBuffer());
  const partial = await call(path, {token: user, headers: {Range: "bytes=5-12"}});
  assert.equal(partial.status, 206);
  assert.deepEqual(new Uint8Array(await partial.arrayBuffer()), bytes.slice(5, 13));
  assert.equal((await call(path, {token: user, headers: {Range: "bytes=0-1,8-12"}})).status, 416);
  assert.equal((await call(base + "/media/metadata.json", {token: user})).status, 404);
  console.log("PASS D1 migration, bootstrap fixture, login/RBAC, private media, CORS, publication, Range.");

  const browser = await chromium.launch({channel: "msedge", headless: true});
  try {
    const page = await browser.newPage();
    const videoRequests = [];
    page.on("response", response => {
      if (response.url().startsWith(workerUrl) && response.url().endsWith(".m4s") && response.ok()) {
        videoRequests.push(new URL(response.url()).pathname);
      }
    });
    await page.goto(siteUrl);
    await page.locator("#username").fill("lab-admin");
    await page.locator("#password").fill(adminPassword);
    await page.getByRole("button", {name: "Đăng nhập"}).click();
    await page.getByRole("button", {name: "Phát online-upload", exact: true}).click();
    await page.waitForFunction(() => document.querySelector("video").currentTime >= 0.2);
    await page.locator("video").evaluate(video => { video.pause(); video.currentTime = 8.1; });
    await page.waitForFunction(() => {
      const video = document.querySelector("video");
      return Math.abs(video.currentTime - 8.1) < 0.2 && !video.seeking && video.readyState >= 2;
    });
    assert.ok(videoRequests.some(path => path.endsWith("seg_000002.m4s")));
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", {name: "Xác nhận đã phát và tua video hiện tại"}).click();
    await page.getByText("Đã lưu kết quả kiểm tra phát và tua.", {exact: true}).waitFor();
    await page.getByRole("button", {name: "Đăng xuất"}).click();
    await page.getByText("Đã đăng xuất.", {exact: true}).waitFor();
    console.log("PASS Edge common login, playback, seek and verification with no Windows Admin service.");
  } finally {
    await browser.close();
  }
  const state = await (await call("/api/local/videos/" + receipt.id, {token: serviceKey})).json();
  assert.ok(state.playback_checked_at);
  const viewers = [user];
  for (const username of ["viewer-two", "viewer-three"]) {
    assert.equal((await call("/api/admin/users", {method: "POST", token: admin,
      body: {username, password: userPassword}})).status, 201);
    viewers.push((await (await login(username, userPassword)).json()).token);
  }
  const concurrent = await Promise.all(viewers.map(token => call(path, {token, headers: {Range: "bytes=0-31"}})));
  for (const response of concurrent) {
    assert.equal(response.status, 206);
    assert.equal((await response.arrayBuffer()).byteLength, 32);
  }
  await db.prepare(
    "WITH RECURSIVE nums(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM nums WHERE n<1001) " +
    "INSERT INTO videos(id,title,original_filename,video_codec,playlist_key,r2_prefix,codecs,status,created_at) " +
    "SELECT printf('00000000-0000-0000-0000-%012x',n),'fixture','fixture.mp4','h264', " +
    "'fixture-key','fixture-prefix','avc1.64001e','READY','2020-01-01T00:00:00.000Z' FROM nums"
  ).run();
  const seen = new Set();
  let cursor;
  do {
    const response = await call("/api/videos?limit=100" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""), {token: user});
    const page = await response.json();
    assert.equal(response.status, 200);
    for (const item of page.items) {
      assert.ok(!seen.has(item.id));
      seen.add(item.id);
    }
    cursor = page.next_cursor;
  } while (cursor);
  assert.equal(seen.size, 1003);
  console.log("PASS three concurrent User media requests and pagination across 1,003 D1 rows.");

  const userPath = "/api/admin/users/" + userRow.id;
  assert.equal((await call(userPath, {method: "PATCH", token: admin, body: {is_active: false}})).status, 204);
  assert.equal((await call("/api/videos", {token: user})).status, 401);
  assert.equal((await login("lab-user", userPassword)).status, 401);
  assert.equal((await call(userPath, {method: "PATCH", token: admin, body: {is_active: true}})).status, 204);
  const second = (await (await login("lab-user", userPassword)).json()).token;
  const newPassword = randomBytes(18).toString("hex");
  assert.equal((await call(userPath + "/reset-password", {method: "POST", token: admin, body: {password: newPassword}})).status, 204);
  assert.equal((await call("/api/auth/me", {token: second})).status, 401);
  const fresh = (await (await login("lab-user", newPassword)).json()).token;
  assert.equal((await call("/api/auth/logout", {method: "POST", token: fresh})).status, 204);
  assert.equal((await call("/api/auth/me", {token: fresh})).status, 401);
  assert.equal((await call(userPath, {method: "DELETE", token: admin})).status, 204);
  for (let index = 0; index < 5; index++) assert.equal((await login("unknown-user", userPassword)).status, 401);
  assert.equal((await login("unknown-user", userPassword)).status, 429);

  assert.equal((await call("/api/admin/videos/" + receipt.id, {method: "DELETE", token: admin})).status, 204);
  assert.equal((await call(base + "/playlist", {token: admin})).status, 404);
  assert.equal((await call("/api/videos/" + receipt.other_id, {token: admin})).status, 200);
  await db.prepare("UPDATE sessions SET expires_at=0").run();
  assert.equal((await call("/api/auth/me", {token: admin})).status, 401);
  console.log("PASS lock/unlock/reset/delete users, logout, rate limiting, expiration and scoped video deletion.");
} finally {
  await worker.dispose();
  website.close();
}

