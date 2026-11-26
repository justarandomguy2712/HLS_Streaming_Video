import { requireService } from "./auth.js";
import { UUID, validId, fail, json, bodyJson, pageSize } from "./http.js";

export const VIDEO_PATH = new RegExp("^/api/videos/(" + UUID + ")(?:/(playlist)|/media/([a-z0-9_.]+))?$");
export const ADMIN_VIDEO_PATH = new RegExp("^/api/admin/videos/(" + UUID + ")(?:/(playback-check))?$");
const KEY = new RegExp("^videos/(" + UUID + ")/(?:(" + UUID + ")/)?playlist\\.m3u8$");
const CURSOR = new RegExp("^(\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d\\.\\d{3}Z)~(" + UUID + ")$");
const CODECS = /^avc1\.[a-f0-9]{6}(?:,mp4a\.40\.2)?$/i;

function keyPrefix(value, id) {
  const match = typeof value === "string" && KEY.exec(value);
  if (!match || match[1] !== id) fail(400, "Object key không thuộc video.");
  return value.slice(0, -"playlist.m3u8".length);
}
function titleValue(value, limit) {
  if (typeof value !== "string" || !value.trim() || value.length > limit || /[\x00-\x1f]/.test(value)) {
    fail(400, "Metadata văn bản không hợp lệ.");
  }
  return value.trim();
}
function metric(value, integer = true) {
  if (value == null) return null;
  if (!Number.isFinite(value) || value < 0 || (integer && !Number.isSafeInteger(value))) {
    fail(400, "Metadata số không hợp lệ.");
  }
  return value;
}
function optionalMatch(value, pattern) {
  if (value == null) return null;
  if (typeof value !== "string" || !pattern.test(value)) fail(400, "Thông số video không hợp lệ.");
  return value;
}
function publicVideo(row) {
  const {id, title, original_filename, original_size, processed_size, duration, resolution,
    frame_rate, video_codec, audio_codec, segment_count, status, created_at, archived_source_size} = row;
  return {id, title, original_filename, original_size, processed_size, duration, resolution,
    frame_rate, video_codec, audio_codec, segment_count, status, created_at, archived_source_size};
}
export async function getVideo(db, id, ready = true) {
  const row = await db.prepare("SELECT * FROM videos WHERE id=?" + (ready ? " AND status='READY'" : ""))
    .bind(id).first();
  if (!row) fail(404, "Video không tồn tại hoặc chưa công bố.");
  return row;
}
export function playback(row, env) {
  return json({
    ...publicVideo(row), codecs: row.codecs, playlist_key: row.playlist_key,
    playback_url: "/api/videos/" + row.id + "/media/playlist.m3u8",
    forward_buffer_seconds: Number(env.PLAYER_FORWARD_BUFFER_SECONDS ?? 60),
    back_buffer_seconds: Number(env.PLAYER_BACK_BUFFER_SECONDS ?? 30)
  });
}
export function publishedPrefix(row) {
  const prefix = keyPrefix(row.playlist_key, row.id);
  if (prefix !== row.r2_prefix) fail(502, "Prefix lưu trữ không hợp lệ.");
  return prefix;
}
export async function listVideos(env, url, admin = false) {
  const limit = pageSize(url, env);
  const token = url.searchParams.get("cursor");
  const match = token && CURSOR.exec(token);
  if (token !== null && !match) fail(400, "Cursor không hợp lệ.");
  const where = admin ? "status IN ('READY','DELETING')" : "status='READY'";
  const args = match ? [match[1], match[2], limit + 1] : [limit + 1];
  const {results} = await env.DB.prepare(
    "SELECT * FROM videos WHERE " + where + (match ? " AND (created_at,id)<(?,?)" : "") +
    " ORDER BY created_at DESC,id DESC LIMIT ?"
  ).bind(...args).all();
  const tail = results[limit - 1];
  return json({
    items: results.slice(0, limit).map(publicVideo),
    next_cursor: results.length > limit ? tail.created_at + "~" + tail.id : null
  });
}
export async function localRoute(request, env, url) {
  requireService(request, env);
  if (url.pathname === "/api/local/health" && request.method === "GET") return json({status: "ok"});
  const state = new RegExp("^/api/local/videos/(" + UUID + ")$").exec(url.pathname);
  if (state && request.method === "GET") {
    const row = await getVideo(env.DB, state[1], false);
    return json({id: row.id, status: row.status, playlist_key: row.playlist_key,
      playback_checked_at: row.playback_checked_at});
  }
  if (url.pathname !== "/api/local/videos" || request.method !== "POST") fail(404, "API không tồn tại.");

  const input = await bodyJson(request);
  const id = input.id;
  if (typeof id !== "string" || !validId.test(id)) fail(400, "UUID không hợp lệ.");
  const prefix = keyPrefix(input.playlist_key, id);
  const title = titleValue(input.title, 200);
  const filename = titleValue(input.original_filename, 255);
  if (!CODECS.test(input.codecs ?? "")) fail(400, "Codec không hỗ trợ.");
  const resolution = optionalMatch(input.resolution, /^\d{1,6}x\d{1,6}$/);
  const fps = optionalMatch(input.frame_rate, /^\d{1,12}\/[1-9]\d{0,11}$/);
  if (typeof input.created_at !== "string" || !CURSOR.test(input.created_at + "~" + id)) {
    fail(400, "Thời gian tạo không hợp lệ.");
  }
  const media = await Promise.all([
    env.VIDEOS.head(input.playlist_key), env.VIDEOS.head(prefix + "init.mp4"),
    env.VIDEOS.head(prefix + "metadata.json"), env.VIDEOS.head(prefix + "seg_000000.m4s")
  ]);
  if (media.some(part => !part || part.size === 0)) fail(409, "Chưa tìm thấy bộ HLS đã lưu.");
  const archive = metric(input.archived_source_size);
  if (archive && (await env.VIDEOS.head(prefix + "source.mp4"))?.size !== archive) {
    fail(409, "MP4 lưu thêm chưa khớp kích thước.");
  }
  const inserted = await env.DB.prepare(
    "INSERT INTO videos(id,title,original_filename,original_size,processed_size,duration,resolution,frame_rate," +
    "video_codec,audio_codec,segment_count,playlist_key,r2_prefix,codecs,status,created_at,archived_source_size) " +
    "VALUES(?,?,?,?,?,?,?,?,'h264',?,?,?, ?,?,'READY',?,?) ON CONFLICT(id) DO NOTHING"
  ).bind(id, title, filename, metric(input.original_size), metric(input.processed_size),
    metric(input.duration, false), resolution, fps, input.codecs.includes(",") ? "aac" : null,
    metric(input.segment_count), input.playlist_key, prefix, input.codecs, input.created_at, archive).run();
  const row = await getVideo(env.DB, id, false);
  if (row.status !== "READY" || row.playlist_key !== input.playlist_key) {
    fail(409, "Video đã bị xóa hoặc UUID đang thuộc bộ media khác.");
  }
  return json({id, status: row.status}, inserted.meta.changes ? 201 : 200);
}

export async function adminVideoRoute(request, env, match) {
  const [, id, action] = match;
  if (action && request.method === "POST") {
    if ((await bodyJson(request)).confirmed !== true) fail(400, "Cần xác nhận đã phát và tua.");
    const updated = await env.DB.prepare(
      "UPDATE videos SET playback_checked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status='READY'"
    ).bind(id).run();
    if (!updated.meta.changes) fail(404, "Video chưa công bố.");
    return new Response(null, {status: 204});
  }
  if (!action && request.method === "PATCH") {
    const {title} = await bodyJson(request);
    const updated = await env.DB.prepare("UPDATE videos SET title=? WHERE id=? AND status='READY'")
      .bind(titleValue(title, 200), id).run();
    if (!updated.meta.changes) fail(404, "Video chưa công bố.");
    return new Response(null, {status: 204});
  }
  if (!action && request.method === "DELETE") {
    const row = await getVideo(env.DB, id, false);
    if (row.status === "DELETED") return new Response(null, {status: 204});
    const prefix = publishedPrefix(row);
    await env.DB.prepare("UPDATE videos SET status='DELETING' WHERE id=? AND status='READY'").bind(id).run();
    const result = await env.VIDEOS.list({prefix, limit: 500});
    const keys = result.objects.map(object => object.key);
    if (keys.some(key => !key.startsWith(prefix))) fail(502, "Phạm vi xóa không hợp lệ.");
    if (keys.length) await env.VIDEOS.delete(keys);
    if (result.truncated) return json({id, status: "DELETING"}, 202);
    await env.DB.prepare("UPDATE videos SET status='DELETED' WHERE id=? AND status='DELETING'").bind(id).run();
    return new Response(null, {status: 204});
  }
  fail(404, "API không tồn tại.");
}

