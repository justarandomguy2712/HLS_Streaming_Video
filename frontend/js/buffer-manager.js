import { segmentIndexAt } from "./playlist-parser.js";

const DEFAULT_FORWARD_SECONDS = 60;
const DEFAULT_BACK_SECONDS = 30;
const TARGET_MEDIA_BYTES = 48 * 1024 * 1024;

export class BufferManager {
  constructor(sourceBuffer, signal, segments, forward, back) {
    this.sourceBuffer = sourceBuffer;
    this.signal = signal;
    this.segments = segments;
    this.forward = Number.isFinite(forward) && forward > 0 ? forward : DEFAULT_FORWARD_SECONDS;
    this.back = Number.isFinite(back) && back >= 0 ? back : DEFAULT_BACK_SECONDS;
  }

  ahead(time) {
    const ranges = this.sourceBuffer.buffered;
    for (let i = 0; i < ranges.length; i++) {
      // Sai số nhỏ ở biên do timestamp video/audio và thời lượng EXTINF.
      if (ranges.start(i) <= time + 0.05 && ranges.end(i) > time) {
        return ranges.end(i) - time;
      }
    }
    return 0;
  }

  update(action) {
    this.signal.throwIfAborted();
    const buffer = this.sourceBuffer;
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        buffer.removeEventListener("updateend", onEnd);
        buffer.removeEventListener("error", onError);
        buffer.removeEventListener("abort", onError);
        this.signal.removeEventListener("abort", onClose);
      };
      const onEnd = () => { cleanup(); resolve(); };
      const onError = () => { cleanup(); reject(new Error("SourceBuffer không xử lý được phân đoạn.")); };
      const onClose = () => { cleanup(); reject(this.signal.reason); };
      buffer.addEventListener("updateend", onEnd);
      buffer.addEventListener("error", onError);
      buffer.addEventListener("abort", onError);
      this.signal.addEventListener("abort", onClose, { once: true });
      try {
        action();
      } catch (error) {
        cleanup();
        reject(error);
      }
    });
  }

  observe(byteLength, seconds) {
    const affordable = TARGET_MEDIA_BYTES * seconds / byteLength;
    if (affordable < this.forward + this.back) {
      this.forward = Math.min(this.forward, Math.max(seconds, affordable * 2 / 3));
      this.back = Math.min(this.back, Math.max(0, affordable - this.forward));
    }
  }

  async append(data, time) {
    try {
      await this.update(() => this.sourceBuffer.appendBuffer(data));
    } catch (error) {
      if (error.name !== "QuotaExceededError") throw error;
      this.forward = Math.max(1, this.forward / 2);
      this.back = Math.min(this.back, 2);
      if (!await this.trim(time, 0)) throw error;
      await this.update(() => this.sourceBuffer.appendBuffer(data));
    }
  }

  async trim(time, retainedSeconds = this.back) {
    const cutoff = this.segments[segmentIndexAt(this.segments, time - retainedSeconds)].start;
    const ranges = this.sourceBuffer.buffered;
    if (!ranges.length || cutoff - ranges.start(0) < 1) return false;
    await this.update(() => this.sourceBuffer.remove(0, cutoff));
    return true;
  }

  async clear() {
    const ranges = this.sourceBuffer.buffered;
    if (ranges.length) {
      const end = ranges.end(ranges.length - 1);
      await this.update(() => this.sourceBuffer.remove(0, end));
    }
  }
}
