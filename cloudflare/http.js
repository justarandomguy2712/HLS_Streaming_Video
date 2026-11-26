export const UUID = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";
export const validId = new RegExp("^" + UUID + "$");

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function fail(status, message) {
  throw new HttpError(status, message);
}

export function json(data, status = 200, headers = {}) {
  return Response.json(data, {status, headers});
}

export async function bodyJson(request) {
  if (!(request.headers.get("Content-Type") || "").match(/^application\/json(?:;|$)/i)) {
    fail(415, "Cần gửi JSON.");
  }
  if (!request.body) fail(400, "Thiếu dữ liệu JSON.");
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let encoded = "", count = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      count += value.byteLength;
      if (count > 16384) {
        await reader.cancel().catch(() => {});
        fail(413, "Dữ liệu JSON vượt giới hạn.");
      }
      encoded += decoder.decode(value, {stream: true});
    }
    let parsed;
    try { parsed = JSON.parse(encoded + decoder.decode()); } catch { fail(400, "JSON không hợp lệ."); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) fail(400, "Cần một object JSON.");
    return parsed;
  } finally {
    reader.releaseLock();
  }
}

export function pageSize(url, env) {
  const size = Number(url.searchParams.get("limit") ?? env.VIDEO_LIST_PAGE_SIZE ?? 20);
  if (!Number.isInteger(size) || size < 1 || size > 100) fail(400, "Giới hạn trang từ 1 đến 100.");
  return size;
}

