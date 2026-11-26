import { randomBytes, randomUUID, createHash, scrypt, timingSafeEqual } from "node:crypto";
import { Buffer } from "node:buffer";
import { bodyJson, fail, json, pageSize, validId } from "./http.js";

const TOKEN = /^[0-9a-f]{64}$/;
const USERNAME = /^[a-z0-9][a-z0-9._-]{2,39}$/;
const SCRYPT = { N: 16384, r: 8, p: 5, maxmem: 64 * 1024 * 1024 };
const WINDOW = 900;
const FAKE_HASH = "scrypt$" + "00".repeat(16) + "$" + "00".repeat(32);
const USER_COLUMNS = "id, username, role, is_active, created_at";

function hash(value) { return createHash("sha256").update(value).digest("hex"); }
function epoch() { return Math.floor(Date.now() / 1000); }
function keyOf(request) { return /^Bearer ([0-9a-f]{64})$/.exec(request.headers.get("Authorization") ?? "")?.[1]; }
function validPassword(value) { return typeof value === "string" && value.length > 0 && Buffer.byteLength(value) <= 256; }
function derive(password, salt) {
  return new Promise((resolve, reject) => {
    scrypt(password, salt, 32, SCRYPT, (error, key) => error ? reject(error) : resolve(key));
  });
}
async function passwordHash(password) {
  if (!validPassword(password)) fail(400, "Mật khẩu User phải có nội dung và tối đa 256 byte UTF-8.");
  const salt = randomBytes(16);
  const key = await derive(password, salt);
  return ["scrypt", salt.toString("hex"), key.toString("hex")].join("$");
}
async function checkPassword(password, stored) {
  const fields = /^scrypt\$([0-9a-f]{32})\$([0-9a-f]{64})$/.exec(stored);
  if (!fields) return false;
  const key = await derive(password, Buffer.from(fields[1], "hex"));
  return timingSafeEqual(key, Buffer.from(fields[2], "hex"));
}

export function requireService(request, env) {
  const supplied = keyOf(request);
  const secret = env.PROCESSING_SERVICE_TOKEN;
  if (typeof secret !== "string" || !TOKEN.test(secret)) fail(503, "Chưa cấu hình khóa companion.");
  if (!supplied || !timingSafeEqual(Buffer.from(supplied, "hex"), Buffer.from(secret, "hex"))) {
    fail(401, "Khóa companion không hợp lệ.");
  }
}

export async function requireSession(request, env, admin = false) {
  const token = keyOf(request);
  if (!token) fail(401, "Cần đăng nhập.");
  const user = await env.DB.prepare(
    "SELECT u.id, u.username, u.role, s.id AS session_id FROM sessions s JOIN users u ON u.id=s.user_id " +
    "WHERE s.token_hash=? AND s.expires_at>? AND s.revoked_at IS NULL AND u.is_active=1"
  ).bind(hash(token), epoch()).first();
  if (!user) fail(401, "Phiên đăng nhập hết hạn hoặc đã bị thu hồi.");
  if (admin && user.role !== "ADMIN") fail(403, "Chức năng chỉ dành cho Admin.");
  return user;
}

async function rateLimit(request, db, username, now) {
  const step = Math.floor(now / WINDOW);
  const keys = ["user:" + hash(username), "addr:" + hash(request.headers.get("CF-Connecting-IP") || "local")];
  const results = await db.batch(keys.map(key => db.prepare(
    "INSERT INTO login_attempts(key,step,hits) VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET " +
    "hits=CASE WHEN step=excluded.step THEN hits+1 ELSE 1 END,step=excluded.step RETURNING hits"
  ).bind(key, step)));
  if (results[0].results[0].hits > 5 || results[1].results[0].hits > 30) {
    return json({detail: "Quá số lần đăng nhập. Hãy thử lại sau."}, 429, {
      "Retry-After": String(WINDOW - now % WINDOW)
    });
  }
  await db.prepare("DELETE FROM login_attempts WHERE key IN (SELECT key FROM login_attempts WHERE step<? LIMIT 50)")
    .bind(step - 1).run();
  return null;
}

export async function authRoute(request, env, path) {
  const db = env.DB;
  if (path === "/api/auth/login" && request.method === "POST") {
    const {username, password} = await bodyJson(request);
    const name = typeof username === "string" ? username.trim().toLowerCase() : "";
    if (!USERNAME.test(name) || typeof password !== "string" || Buffer.byteLength(password) > 256) {
      fail(400, "Tên đăng nhập hoặc mật khẩu sai định dạng.");
    }
    const now = epoch();
    const blocked = await rateLimit(request, db, name, now);
    if (blocked) return blocked;
    const user = await db.prepare("SELECT * FROM users WHERE username=?").bind(name).first();
    const correct = await checkPassword(password, user?.password_hash ?? FAKE_HASH);
    if (!user?.is_active || !correct) fail(401, "Thông tin đăng nhập không hợp lệ.");
    const ttl = Number(env.SESSION_TTL_SECONDS ?? 28800);
    if (!Number.isInteger(ttl) || ttl < 60 || ttl > 86400) fail(503, "Thời hạn phiên chưa cấu hình đúng.");
    const token = randomBytes(32).toString("hex");
    const [, inserted] = await db.batch([
      db.prepare("DELETE FROM sessions WHERE id IN (SELECT id FROM sessions WHERE expires_at<=? LIMIT 50)").bind(now),
      db.prepare("INSERT INTO sessions(id,user_id,token_hash,expires_at) SELECT ?,id,?,? FROM users " +
        "WHERE id=? AND is_active=1 AND password_hash=?").bind(randomUUID(), hash(token), now + ttl, user.id, user.password_hash),
      // Giữ bộ đếm IP; đăng nhập đúng chỉ xóa lỗi cho tài khoản của người đó.
      db.prepare("DELETE FROM login_attempts WHERE key=?").bind("user:" + hash(name))
    ]);
    if (!inserted.meta.changes) fail(401, "Thông tin đăng nhập đã thay đổi.");
    return json({token, expires_at: now + ttl, user: {id: user.id, username: user.username, role: user.role}});
  }
  if (path === "/api/auth/me" && request.method === "GET") {
    const {id, username, role} = await requireSession(request, env);
    return json({id, username, role});
  }
  if (path === "/api/auth/logout" && request.method === "POST") {
    const session = await requireSession(request, env);
    await db.prepare("UPDATE sessions SET revoked_at=? WHERE id=?").bind(epoch(), session.session_id).run();
    return new Response(null, {status: 204});
  }
  fail(404, "API không tồn tại.");
}

export async function usersRoute(request, env, url, match) {
  await requireSession(request, env, true);
  const db = env.DB;
  const [, id, action] = match;
  const method = request.method;

  if (!id && method === "GET") {
    const cursor = url.searchParams.get("cursor") || "";
    if (cursor && !validId.test(cursor)) fail(400, "Cursor không hợp lệ.");
    const limit = pageSize(url, env);
    const {results} = await db.prepare(
      "SELECT " + USER_COLUMNS + " FROM users WHERE id>? ORDER BY id LIMIT ?"
    ).bind(cursor, limit + 1).all();
    return json({items: results.slice(0, limit), next_cursor: results.length > limit ? results[limit - 1].id : null});
  }

  if (!id && method === "POST") {
    const {username, password} = await bodyJson(request);
    const name = typeof username === "string" ? username.trim().toLowerCase() : "";
    if (!USERNAME.test(name)) fail(400, "Tên đăng nhập cần 3–40 ký tự ASCII hợp lệ.");
    const existing = await db.prepare("SELECT id FROM users WHERE username=?").bind(name).first();
    if (existing) fail(409, "Tên đăng nhập đã tồn tại.");
    const result = await db.prepare("INSERT INTO users(id,username,password_hash,role) VALUES(?,?,?,'USER') " +
      "ON CONFLICT(username) DO NOTHING RETURNING " + USER_COLUMNS).bind(randomUUID(), name, await passwordHash(password)).first();
    if (!result) fail(409, "Tên đăng nhập đã tồn tại.");
    return json(result, 201);
  }

  if (id && !action && method === "DELETE") {
    const result = await db.prepare("DELETE FROM users WHERE id=? AND role='USER'").bind(id).run();
    if (!result.meta.changes) fail(404, "Không tìm thấy tài khoản User.");
    return new Response(null, {status: 204});
  }

  if (id && ((!action && method === "PATCH") || (action === "reset-password" && method === "POST"))) {
    const body = await bodyJson(request);
    const reset = Boolean(action);
    if (!reset && typeof body.is_active !== "boolean") fail(400, "is_active phải là boolean.");
    const old = await db.prepare("SELECT id FROM users WHERE id=? AND role='USER'").bind(id).first();
    if (!old) fail(404, "Không tìm thấy tài khoản User.");
    const value = reset ? await passwordHash(body.password) : Number(body.is_active);
    const field = reset ? "password_hash" : "is_active";
    const commands = [db.prepare("UPDATE users SET " + field + "=? WHERE id=? AND role='USER'").bind(value, id)];
    if (reset || !body.is_active) commands.push(db.prepare(
      "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL"
    ).bind(epoch(), id));
    const [updated] = await db.batch(commands);
    if (!updated.meta.changes) fail(404, "Không tìm thấy tài khoản User.");
    return new Response(null, {status: 204});
  }
  fail(404, "API không tồn tại.");
}

