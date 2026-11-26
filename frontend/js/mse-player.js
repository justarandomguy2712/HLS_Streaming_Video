import { BufferManager } from "./buffer-manager.js";
import { parsePlaylist, segmentIndexAt } from "./playlist-parser.js";
import { requestSignal } from "./request-signal.js";
import { fetchResource, loadSegment } from "./segment-loader.js";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const SEEK_PREROLL_SECONDS = 0.25;

function sourceOpen(source, video, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      source.removeEventListener("sourceopen", onOpen);
      video.removeEventListener("error", onError);
      signal.removeEventListener("abort", onAbort);
    };
    const onOpen = () => { cleanup(); resolve(); };
    const onError = () => { cleanup(); reject(new Error("Không mở được MediaSource.")); };
    const onAbort = () => { cleanup(); reject(signal.reason); };
    source.addEventListener("sourceopen", onOpen, { once: true });
    video.addEventListener("error", onError, { once: true });
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export class MSEPlayer {
  constructor(video, showStatus, {
    endpoint = id => new URL(`/api/videos/${id}/playback`, document.baseURI).href,
    requestOptions, onUnauthorized
  } = {}) {
    this.video = video;
    this.showStatus = showStatus;
    this.endpoint = endpoint;
    this.requestOptions = requestOptions;
    this.onUnauthorized = onUnauthorized;
    this.lifetime = new AbortController();
    this.nextIndex = 0;
    this.pendingSeek = null;
    this.seekTarget = null;
    this.running = false;
    this.failed = false;
  }

  async start(id) {
    if (!UUID.test(id)) throw new Error("Video ID phải có dạng UUID.");
    const signal = this.lifetime.signal;
    this.playbackEndpoint = this.endpoint(id);
    const { data: record } = await fetchResource(this.playbackEndpoint, "json", signal, this.requestOptions);
    if (!record.playback_url || !record.codecs) {
      throw new Error("Video chưa có playlist phát lại. Hãy kiểm tra trạng thái xuất bản lên R2.");
    }
    if (!/^avc1\.[0-9a-f]{6}(?:,mp4a\.40\.2)?$/i.test(record.codecs)) {
      throw new Error("Backend trả thông tin codec không hợp lệ.");
    }
    const mime = `video/mp4; codecs="${record.codecs}"`;
    const standard = globalThis.MediaSource;
    const managed = globalThis.ManagedMediaSource;
    const Source = standard?.isTypeSupported(mime) ? standard :
      managed?.isTypeSupported(mime) ? managed : null;
    if (!Source) {
      throw new Error(standard || managed
        ? `Thiết bị chưa hỗ trợ phát codec ${record.codecs} qua MSE.`
        : "Trình duyệt thiếu MediaSource và ManagedMediaSource. Trên iPhone, cần iOS 17.1 trở lên.");
    }
    this.record = record;
    try {
      this.timeline = await this.loadTimeline(record, signal);
    } catch (error) {
      if (![401, 403].includes(error.status)) throw error;
      await this.refreshTimeline(signal);
    }

    signal.throwIfAborted();
    // WebKit cần tắt remote playback nếu không có nguồn HLS riêng cho AirPlay.
    this.previousRemotePlayback = this.video.disableRemotePlayback;
    if (Source === managed && Source !== standard) this.video.disableRemotePlayback = true;
    const source = new Source();
    this.source = source;
    const opened = sourceOpen(source, this.video, signal);
    this.objectUrl = URL.createObjectURL(source);
    this.video.src = this.objectUrl;
    await opened;
    this.buffer = new BufferManager(
      source.addSourceBuffer(mime), signal, this.timeline.segments,
      record.forward_buffer_seconds, record.back_buffer_seconds
    );
    source.duration = this.timeline.duration;
    await this.buffer.append(await this.loadMedia(null, signal), 0);
    await this.appendNext(0);
    this.endIfComplete();
    this.bindEvents();
    this.showStatus(this.video.paused ? "Đã nạp phân đoạn đầu. Bấm Play để phát." : "Đang phát.");
    if (this.video.seeking) this.onSeek();
    if (!this.video.paused) this.scheduleFill();
  }

  async loadTimeline(record, signal) {
    const url = new URL(record.playback_url, this.playbackEndpoint).href;
    const playlist = await fetchResource(url, "text", signal, this.requestOptions);
    return parsePlaylist(playlist.data, playlist.url);
  }

  async refreshTimeline(signal) {
    const { data: record } = await fetchResource(this.playbackEndpoint, "json", signal, this.requestOptions);
    if (!record.playback_url || record.codecs !== this.record.codecs ||
        record.playlist_key !== this.record.playlist_key) {
      throw new Error("Video đã đổi nguồn phát. Hãy mở lại video.");
    }
    const timeline = await this.loadTimeline(record, signal);
    if (this.timeline && (timeline.segments.length !== this.timeline.segments.length ||
        timeline.segments.some((part, index) => part.end !== this.timeline.segments[index].end))) {
      throw new Error("Timeline đã thay đổi. Hãy mở lại video.");
    }
    this.timeline = timeline;
  }

  async loadMedia(index, signal) {
    const currentUrl = () => index === null ? this.timeline.initUrl : this.timeline.segments[index].url;
    try {
      return await loadSegment(currentUrl(), signal, this.requestOptions);
    } catch (error) {
      if (![401, 403].includes(error.status)) throw error;
      await this.refreshTimeline(signal);
      return loadSegment(currentUrl(), signal, this.requestOptions);
    }
  }

  bindEvents() {
    const options = { signal: this.lifetime.signal };
    for (const event of ["play", "timeupdate", "waiting"]) {
      this.video.addEventListener(event, () => this.scheduleFill(), options);
    }
    this.video.addEventListener("seeking", () => this.onSeek(), options);
    this.video.addEventListener("playing", () => this.showStatus("Đang phát."), options);
    this.video.addEventListener("pause", () => {
      if (!this.failed && !this.video.ended) this.showStatus("Đã tạm dừng.");
    }, options);
    this.video.addEventListener("ended", () => this.showStatus("Đã phát hết. Bấm Play để phát lại."), options);
    this.video.addEventListener("error", () => this.fail(new Error("Trình duyệt không giải mã được video.")), options);
    this.source.addEventListener("sourceclose", () => this.fail(new Error("MediaSource đã đóng.")), options);
    this.source.addEventListener("startstreaming", () => this.scheduleFill(), options);
  }

  onSeek() {
    if (this.failed) return;
    const time = Math.max(0, Math.min(this.video.currentTime, this.timeline.duration - 0.05));
    if (this.pendingSeek !== null || this.seekTarget !== null || !this.buffer.ahead(time)) {
      this.pendingSeek = time;
      this.segmentRequest?.abort();
      this.showStatus("Đang nạp phân đoạn tại vị trí tua…");
    }
    this.scheduleFill();
  }

  scheduleFill() {
    if (this.running || this.failed || this.lifetime.signal.aborted) return;
    this.running = true;
    void this.fill().catch(error => this.fail(error)).finally(() => {
      this.running = false;
      if (this.pendingSeek !== null) this.scheduleFill();
    });
  }

  async fill() {
    while (!this.lifetime.signal.aborted) {
      if (this.pendingSeek !== null) {
        const target = this.pendingSeek;
        this.pendingSeek = null;
        this.seekTarget = target;
        this.deferredSegment = null;
        await this.buffer.clear();
        this.nextIndex = segmentIndexAt(this.timeline.segments, target - SEEK_PREROLL_SECONDS);
        if (this.pendingSeek !== null) continue;
      }

      if (this.endIfComplete()) return;
      const time = this.seekTarget ?? this.video.currentTime;
      if (this.seekTarget === null && (this.video.paused || this.buffer.ahead(time) >= this.buffer.forward)) return;

      await this.buffer.trim(time);
      if (this.pendingSeek !== null) continue;
      try {
        await this.appendNext(time);
      } catch (error) {
        if (error.name === "AbortError" && this.pendingSeek !== null) continue;
        if (error.name === "QuotaExceededError" && this.seekTarget === null &&
            this.buffer.ahead(this.video.currentTime) > 0.5) return;
        throw error;
      }
      if (this.seekTarget !== null && this.buffer.ahead(this.seekTarget)) {
        this.seekTarget = null;
        if (this.video.paused) this.showStatus("Đã tua. Bấm Play để tiếp tục.");
      } else if (this.seekTarget !== null) {
        const next = this.timeline.segments[this.nextIndex];
        if (next && next.start > this.seekTarget + Math.max(this.buffer.forward, 12)) {
          throw new Error("Không tìm được dữ liệu giải mã tại vị trí tua.");
        }
      }
    }
  }

  async appendNext(time) {
    const request = new AbortController();
    this.segmentRequest = request;
    const scope = requestSignal([this.lifetime.signal, request.signal]);
    const { signal } = scope;
    try {
      const data = this.deferredSegment ?? await this.loadMedia(this.nextIndex, signal);
      signal.throwIfAborted();
      const part = this.timeline.segments[this.nextIndex];
      this.buffer.observe(data.byteLength, part.end - part.start);
      this.deferredSegment = data;
      await this.buffer.append(data, time);
      this.deferredSegment = null;
      this.nextIndex++;
    } finally {
      scope.close();
      this.segmentRequest = null;
    }
  }

  endIfComplete() {
    if (this.nextIndex < this.timeline.segments.length || this.pendingSeek !== null) return false;
    if (this.source.readyState === "open") this.source.endOfStream();
    this.seekTarget = null;
    return true;
  }

  fail(error) {
    if (this.failed || this.lifetime.signal.aborted) return;
    this.failed = true;
    this.close();
    if (error.status === 401 && this.onUnauthorized) {
      this.onUnauthorized();
      return;
    }
    this.showStatus(`${error.message} Bấm Mở video để thử lại.`);
  }

  close() {
    this.lifetime.abort();
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    if (this.previousRemotePlayback !== undefined) {
      this.video.disableRemotePlayback = this.previousRemotePlayback;
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.deferredSegment = null;
    this.objectUrl = null;
    this.source = null;
    this.buffer = null;
    this.timeline = null;
  }
}
