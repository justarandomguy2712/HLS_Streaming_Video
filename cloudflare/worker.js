import { UUID, HttpError, fail, json } from "./http.js";
import { authRoute, requireSession, usersRoute } from "./auth.js";
import { ADMIN_VIDEO_PATH, VIDEO_PATH, adminVideoRoute, getVideo, listVideos,
  localRoute, playback, publishedPrefix } from "./catalog.js";

const USERS_PATH = new RegExp("^/api/admin/users(?:/(" + UUID + ")(?:/(reset-password))?)?$");
const MEDIA_SEGMENT = /^seg_[0-9]{6,}\.m4s$/;

function corsResponse(response, origin) {
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", "private, no-store");
  headers.set("Vary", "Origin, Authorization");
  headers.set("X-Content-Type-Options", "nosniff");
  if (origin) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Access-Control-Expose-Headers", "Accept-Ranges, Content-Range, Content-Location, ETag");
  }
  return new Response(response.body, { status: response.status, headers });
}

function mediaType(name) {
  if (name === "playlist.m3u8") return "application/vnd.apple.mpegurl";
  if (name === "init.mp4") return "video/mp4";
  return MEDIA_SEGMENT.test(name) ? "video/iso.segment" : null;
}

function byteRange(value, size) {
  const fields = /^bytes=(\d{0,20})-(\d{0,20})$/.exec(value ?? "");
  if (!fields || (!fields[1] && !fields[2]) || size === 0) return null;
  if (!fields[1]) {
    const length = Math.min(Number(fields[2]), size);
    return length > 0 ? { offset: size - length, length } : null;
  }
  const offset = Number(fields[1]);
  const end = fields[2] ? Math.min(Number(fields[2]), size - 1) : size - 1;
  return offset <= end ? { offset, length: end - offset + 1 } : null;
}

async function serveMedia(request, bucket, key, type, location) {
  const headers = new Headers({ "Content-Type": type, "Accept-Ranges": "bytes" });
  if (location) headers.set("Content-Location", location);
  let range = null;
  let info;
  const rangeHeader = request.headers.get("Range");
  if (rangeHeader || request.method === "HEAD") {
    info = await bucket.head(key);
    if (!info) return json({ detail: "Object không tồn tại." }, 404);
    const ifRange = request.headers.get("If-Range");
    if (rangeHeader && (!ifRange || ifRange === info.httpEtag)) {
      range = byteRange(rangeHeader, info.size);
      if (!range) {
        headers.set("Content-Range", `bytes */${info.size}`);
        return new Response(null, { status: 416, headers });
      }
      headers.set("Content-Range", `bytes ${range.offset}-${range.offset + range.length - 1}/${info.size}`);
    }
  }
  const object = request.method === "HEAD" ? info : await bucket.get(key, range ? { range } : undefined);
  if (!object) return json({ detail: "Object không tồn tại." }, 404);
  headers.set("ETag", object.httpEtag);
  headers.set("Content-Length", String(range ? range.length : object.size));
  return new Response(request.method === "HEAD" ? null : object.body, { status: range ? 206 : 200, headers });
}

async function route(request, env, url) {
  const path = url.pathname;
  if (path.startsWith("/api/auth/")) return authRoute(request, env, path);
  if (path.startsWith("/api/local/")) return localRoute(request, env, url);
  const users = USERS_PATH.exec(path);
  if (users) return usersRoute(request, env, url, users);
  if (path === "/api/admin/videos" && request.method === "GET") {
    await requireSession(request, env, true);
    return listVideos(env, url, true);
  }
  const management = ADMIN_VIDEO_PATH.exec(path);
  if (management) {
    await requireSession(request, env, true);
    return adminVideoRoute(request, env, management);
  }
  if (!["GET", "HEAD"].includes(request.method)) fail(405, "Phương thức không được hỗ trợ.");
  await requireSession(request, env);
  if (path === "/api/videos" && request.method === "GET") return listVideos(env, url);
  const match = VIDEO_PATH.exec(path);
  if (!match) fail(404, "API không tồn tại.");
  const [, id, alias, filename] = match;
  const name = alias ? "playlist.m3u8" : filename;
  const type = name && mediaType(name);
  if (name && !type) fail(404, "Tệp media không tồn tại.");
  if (!name && request.method === "HEAD") fail(405, "Metadata chỉ hỗ trợ GET.");
  const row = await getVideo(env.DB, id);
  if (!name) return playback(row, env);
  const prefix = publishedPrefix(row);
  const canonical = "/api/videos/" + id + "/media/playlist.m3u8";
  return serveMedia(request, env.VIDEOS, prefix + name, type, alias ? canonical : null);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin");
    if (url.protocol !== "https:" && !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
      return corsResponse(json({detail: "Worker yêu cầu HTTPS."}, 400));
    }
    const origins = (env.ALLOWED_ORIGINS ?? "").split(",").map(value => value.trim());
    if (origin && !origins.includes(origin)) return corsResponse(json({ detail: "Origin không được phép." }, 403));
    if (request.method === "OPTIONS") {
      const headers = {
        "Access-Control-Allow-Methods": "GET, HEAD, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, Range, If-Range",
        "Access-Control-Max-Age": "600"
      };
      return corsResponse(new Response(null, { status: 204, headers }), origin);
    }
    if (!["GET", "HEAD", "POST", "PATCH", "DELETE"].includes(request.method)) {
      return corsResponse(json({ detail: "Phương thức không được hỗ trợ." }, 405, { Allow: "GET, HEAD, POST, PATCH, DELETE, OPTIONS" }), origin);
    }
    try {
      const response = await route(request, env, url);
      if (request.method === "HEAD" && response.body) {
        await response.body.cancel();
        return corsResponse(new Response(null, { status: response.status, headers: response.headers }), origin);
      }
      return corsResponse(response, origin);
    } catch (error) {
      if (error instanceof HttpError) {
        return corsResponse(json({ detail: error.message }, error.status), origin);
      }
      console.error("Online player request failed", error.name);
      return corsResponse(json({ detail: "Worker chưa đọc được dữ liệu trên R2." }, 502), origin);
    }
  }
};
