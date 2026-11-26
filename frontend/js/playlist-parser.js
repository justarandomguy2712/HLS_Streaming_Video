// Đọc media playlist VOD fMP4 do FFmpeg của dự án tạo.
export function parsePlaylist(text, playlistUrl) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).map(line => line.trim());
  if (lines.shift() !== "#EXTM3U") throw new Error("Playlist HLS thiếu #EXTM3U.");

  const segments = [];
  let initUrl;
  let pendingDuration;
  let duration = 0;
  let ended = false;
  const resolve = reference => {
    const url = new URL(reference, playlistUrl);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("URI phân đoạn HLS không hợp lệ.");
    }
    return url.href;
  };

  for (const line of lines) {
    if (!line) continue;
    if (ended || /^#EXT-X-(STREAM-INF|I-FRAME-STREAM-INF|MEDIA:|KEY:|BYTERANGE:|DISCONTINUITY|GAP)/.test(line)) {
      throw new Error("Trình phát cần một media playlist VOD fMP4 với URI tệp riêng.");
    }
    if (line.startsWith("#EXT-X-MAP:")) {
      const map = line.match(/^#EXT-X-MAP:URI="([^"]+)"$/);
      if (!map || initUrl || segments.length || pendingDuration !== undefined) {
        throw new Error("Playlist cần một init segment dùng chung.");
      }
      initUrl = resolve(map[1]);
    } else if (line.startsWith("#EXTINF:")) {
      const value = line.match(/^#EXTINF:(\d+(?:\.\d+)?),/);
      if (!value || pendingDuration !== undefined) throw new Error("EXTINF không hợp lệ.");
      pendingDuration = Number(value[1]);
      if (!Number.isFinite(pendingDuration) || pendingDuration <= 0) {
        throw new Error("Thời lượng phân đoạn không hợp lệ.");
      }
    } else if (line === "#EXT-X-ENDLIST") {
      ended = true;
    } else if (!line.startsWith("#")) {
      if (!initUrl || pendingDuration === undefined) {
        throw new Error("Playlist thiếu init segment hoặc EXTINF.");
      }
      const end = duration + pendingDuration;
      if (!Number.isFinite(end) || end <= duration) throw new Error("Timeline HLS không hợp lệ.");
      segments.push({ url: resolve(line), start: duration, end });
      duration = end;
      pendingDuration = undefined;
    }
  }

  if (!ended || !segments.length || pendingDuration !== undefined) {
    throw new Error("Playlist VOD chưa hoàn chỉnh.");
  }
  return { initUrl, segments, duration };
}

export function segmentIndexAt(segments, time) {
  let lo = 0;
  let hi = segments.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (time < segments[mid].end) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}
