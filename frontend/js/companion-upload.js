import { requestSignal } from "./request-signal.js";

const LOOPBACK = "http://127.0.0.1:8765";
const PHASES = {
  QUEUED: "Đang chờ", ANALYZING: "Đang phân tích video", PROCESSING: "Đang xử lý FFmpeg",
  UPLOADING: "Đang upload lên R2", VERIFYING: "Đang xác minh dữ liệu",
  PUBLISHING: "Đang cập nhật danh sách video", CLEANING_UP: "Đang giải phóng ổ cứng",
  READY: "Video đã sẵn sàng", FAILED: "Xử lý thất bại",
};
function size(value) {
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)} GB`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)} MB`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(2)} KB`;
  return `${value} B`;
}
const clock = seconds => {
  const rounded = Math.max(0, Math.round(seconds));
  return [Math.floor(rounded / 3600), Math.floor(rounded / 60) % 60, rounded % 60]
    .map(part => String(part).padStart(2, "0")).join(":");
};
const percentage = (value, total) => Math.max(0, Math.min(100, 100 * value / total));

function progressLine(label) {
  const row = document.createElement("p");
  const bar = document.createElement("progress");
  const status = document.createElement("span");
  bar.max = 100;
  bar.setAttribute("aria-label", label);
  row.append(label + " ", bar, " ", status);
  return {row, bar, status};
}

function renderProgress(line, percent, description = "") {
  if (Number.isFinite(percent)) {
    line.bar.value = percent;
  } else {
    line.bar.removeAttribute("value");
  }
  line.status.textContent = Number.isFinite(percent) ? `${percent.toFixed(1)}% ${description}` : description;
}

function renderSystem(metrics) {
  return [
    metrics.gpu_name,
    metrics.encode_percent != null && `GPU encode ${metrics.encode_percent}%`,
    metrics.decode_percent != null && `GPU decode ${metrics.decode_percent}%`,
    metrics.vram_used_mib != null && (metrics.vram_total_mib != null
      ? `VRAM ${metrics.vram_used_mib}/${metrics.vram_total_mib} MiB` : `VRAM ${metrics.vram_used_mib} MiB`),
    metrics.cpu_percent != null && `CPU ${metrics.cpu_percent.toFixed(1)}%`,
    metrics.ram_used_bytes != null && `RAM ${size(metrics.ram_used_bytes)}/${size(metrics.ram_total_bytes)}`,
  ].filter(Boolean).join(" · ");
}

export function attachCompanion(reloadVideos) {
  const byId = id => document.getElementById(id);
  const form = byId("companion-upload");
  const fields = byId("companion-inputs");
  const input = byId("companion-file");
  const title = byId("companion-title");
  const info = byId("companion-file-info");
  const submit = byId("companion-submit");
  const status = byId("companion-status");
  const connectButton = byId("companion-connect");
  const transfer = byId("companion-transfer");
  const system = byId("companion-system");
  const jobs = byId("companion-jobs");
  const cards = new Map();
  const published = new Set();
  let access, file, timer, polling = false, pollAgain = false, sending = false, connected = false;
  const isLocal = location.origin === LOOPBACK;
  byId("companion-local").hidden = isLocal;

  async function request(path, auth, method = "GET") {
    const scope = requestSignal([auth.abort.signal], 10_000);
    try {
      const response = await fetch(LOOPBACK + path, {
        method, redirect: "error", credentials: "omit", cache: "no-store",
        headers: {Authorization: `Bearer ${auth.token}`}, signal: scope.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      return data;
    } finally {
      scope.close();
    }
  }

  async function retry(id, button) {
    const auth = access;
    button.disabled = true;
    try {
      await request(`/api/companion/jobs/${id}/retry`, auth, "POST");
      if (access === auth) {
        status.textContent = "Đã đưa tác vụ vào hàng đợi.";
        void poll();
      }
    } catch (error) {
      if (access === auth) status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  function renderJobs(items) {
    const ids = new Set(items.map(item => item.id));
    for (const [id, card] of cards) {
      if (!ids.has(id)) {
        card.row.remove();
        cards.delete(id);
      }
    }
    let publication = false;
    for (const job of items) {
      if (!cards.has(job.id)) {
        const row = document.createElement("article");
        const name = document.createElement("h3");
        const details = document.createElement("p");
        const processing = progressLine("FFmpeg");
        const uploading = progressLine("Upload R2");
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Thử lại";
        button.addEventListener("click", () => void retry(job.id, button));
        row.append(name, details, processing.row, uploading.row, button);
        jobs.append(row);
        cards.set(job.id, {row, name, details, processing, uploading, button});
      }
      const card = cards.get(job.id);
      card.name.textContent = job.title;
      const details = [PHASES[job.phase], job.mode?.toUpperCase(), job.encoder,
        job.hardware_decode && "giải mã CUDA",
        job.elapsed_seconds != null && `Đã chạy ${clock(job.elapsed_seconds)}`,
        job.segments_created != null && `Segment tạo ${job.segments_created}, đã upload và xác minh ${job.segments_uploaded}`,
        {running: "Đang kiểm tra SHA-256", verified: "Đã xác minh SHA-256", incomplete: "Xác minh chưa hoàn tất"}[job.verification],
        {running: "Đang dọn tệp tạm", done: "Đã dọn tệp tạm", failed: "Dọn tệp tạm còn chờ"}[job.cleanup],
        job.error];
      card.details.textContent = details.filter(Boolean).join(" · ");
      card.button.hidden = job.phase !== "FAILED" && job.cleanup !== "failed";
      card.processing.row.hidden = job.phase === "QUEUED" || job.phase === "ANALYZING";
      card.uploading.row.hidden = card.processing.row.hidden;
      const processingEta = !job.ffmpeg_done && job.duration && job.speed
        ? clock((job.duration - job.media_seconds) / job.speed) : null;
      const uploadEta = job.total_bytes && job.bytes_per_second > 0 && job.transfer_bytes < job.total_bytes
        ? clock((job.total_bytes - job.transfer_bytes) / job.bytes_per_second) : null;
      renderProgress(card.processing, job.ffmpeg_done ? 100 :
        job.duration ? Math.min(99.9, percentage(job.media_seconds, job.duration)) : null,
        [!job.ffmpeg_done && job.speed && `${job.speed.toFixed(2)}×`, processingEta && `ETA ${processingEta}`]
          .filter(Boolean).join(" · "));
      renderProgress(card.uploading, job.total_bytes ? percentage(job.uploaded_bytes, job.total_bytes) : null,
        job.transfer_bytes == null ? "" : [
          `R2 xác nhận ${size(job.uploaded_bytes)}` + (job.total_bytes ? ` / ${size(job.total_bytes)}` : " · Chưa biết tổng"),
          job.transfer_bytes > job.uploaded_bytes && `Đã gửi ${size(job.transfer_bytes)}`,
          job.bytes_per_second != null && `${(job.bytes_per_second / 1e6).toFixed(2)} MB/s trung bình`,
          uploadEta && `ETA ${uploadEta}`,
        ].filter(Boolean).join(" · "));
      if (job.phase === "READY" && !published.has(job.id)) {
        published.add(job.id);
        publication = true;
      }
    }
    if (publication) void reloadVideos();
  }

  async function poll() {
    if (!access) return;
    if (polling) {
      pollAgain = true;
      return;
    }
    clearTimeout(timer);
    const auth = access;
    polling = true;
    try {
      const data = await request("/api/companion/jobs", auth);
      if (access !== auth) return;
      if (!connected) status.textContent = "Đã kết nối companion.";
      connected = true;
      form.hidden = false;
      renderJobs(data.jobs);
      system.textContent = renderSystem(data.system);
      if (sending || data.jobs.some(job => !["READY", "FAILED"].includes(job.phase))) {
        timer = setTimeout(() => void poll(), Math.max(100, data.interval_ms));
      }
    } catch (error) {
      if (access !== auth) return;
      connected = false;
      status.textContent = error instanceof TypeError
        ? "Không kết nối được companion. Kiểm tra ứng dụng Windows và quyền mạng cục bộ, hoặc mở companion cục bộ."
        : error.message;
    } finally {
      if (access === auth) {
        polling = false;
        if (pollAgain) {
          pollAgain = false;
          void poll();
        }
      }
    }
  }

  function select(next) {
    file = null;
    if (next && /\.mp4$/i.test(next.name) && next.size) {
      file = next;
      info.textContent = `${file.name} · ${size(file.size)}`;
    } else {
      info.textContent = "Chọn một tệp MP4 có dữ liệu.";
    }
    submit.disabled = !file;
    submit.textContent = "Upload & Process";
  }

  function send(auth) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", LOOPBACK + "/api/companion/jobs");
      xhr.responseType = "json";
      xhr.setRequestHeader("Authorization", `Bearer ${auth.token}`);
      xhr.setRequestHeader("Content-Type", "video/mp4");
      xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
      xhr.setRequestHeader("X-Video-Title", encodeURIComponent(title.value.trim()));
      const bar = transfer.querySelector("progress");
      const text = transfer.querySelector("span");
      bar.value = 0;
      text.textContent = "";
      transfer.hidden = false;
      const abort = () => xhr.abort();
      auth.abort.signal.addEventListener("abort", abort, {once: true});
      xhr.upload.onprogress = event => {
        const percent = percentage(event.loaded, file.size);
        bar.value = percent;
        text.textContent = `${percent.toFixed(1)}% · ${size(event.loaded)} / ${size(file.size)}`;
      };
      xhr.onload = () => xhr.status === 202 ? resolve(xhr.response) :
        reject(new Error(xhr.response?.detail || `HTTP ${xhr.status}`));
      xhr.onerror = () => reject(new Error("Gửi MP4 thất bại. Kiểm tra kết nối hoặc quyền mạng cục bộ."));
      xhr.onabort = () => reject(new DOMException("Đã hủy gửi MP4.", "AbortError"));
      xhr.onloadend = () => auth.abort.signal.removeEventListener("abort", abort);
      xhr.send(file);
    });
  }

  connectButton.addEventListener("click", () => void poll());
  input.addEventListener("change", () => select(input.files[0]));
  fields.addEventListener("dragover", event => event.preventDefault());
  fields.addEventListener("drop", event => {
    event.preventDefault();
    if (!sending) select(event.dataTransfer.files.length === 1 ? event.dataTransfer.files[0] : null);
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (sending || !file || !access) return;
    const auth = access;
    sending = fields.disabled = true;
    try {
      const result = await send(auth);
      if (auth !== access) return;
      status.textContent = `Windows đã tiếp nhận MP4 · ${result.id}`;
      form.reset();
      select(null);
      info.textContent = "";
      void poll();
    } catch (error) {
      if (auth !== access) return;
      status.textContent = error.message;
      submit.textContent = "Thử gửi lại";
    } finally {
      if (auth === access) sending = fields.disabled = false;
    }
  });

  return {
    async login(auth) {
      access = auth;
      if (isLocal) {
        void poll();
        return;
      }
      try {
        const permission = await navigator.permissions.query({name: "loopback-network"});
        if (access === auth && permission.state === "granted") void poll();
      } catch {
        // Trình duyệt có thể hỗ trợ kết nối qua nút mà không có tên quyền này.
      }
    },
    reset() {
      access = file = null;
      clearTimeout(timer);
      connected = polling = pollAgain = sending = fields.disabled = false;
      form.hidden = transfer.hidden = true;
      form.reset();
      submit.disabled = true;
      status.textContent = info.textContent = system.textContent = "";
      jobs.replaceChildren();
      cards.clear();
      published.clear();
    },
  };
}
