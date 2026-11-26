const list = document.querySelector("#video-library");
const status = document.querySelector("#library-status");
const more = document.querySelector("#library-more");
let nextCursor = null;
let activeRequest;

function renderVideo(video) {
  const item = document.createElement("li");
  item.dataset.videoId = video.id;
  const label = document.createElement("span");
  const details = [video.title, video.status];
  if (video.duration !== null) details.push(`${video.duration.toLocaleString("vi-VN")} giây`);
  if (video.segment_count !== null) details.push(`${video.segment_count} đoạn`);
  label.textContent = details.join(" · ");
  const play = document.createElement("button");
  play.type = "button";
  play.textContent = "Play";
  play.disabled = !video.playable;
  play.setAttribute("aria-label", `Play ${video.title}`);
  play.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("play-library-video", { detail: video.id }));
  });
  item.append(label, " ", play);
  list.append(item);
}

async function loadPage(reset) {
  if (!reset && nextCursor === null) return;
  activeRequest?.abort();
  const request = new AbortController();
  activeRequest = request;
  more.disabled = true;
  try {
    const url = new URL("/api/videos", location.origin);
    if (!reset) url.searchParams.set("cursor", nextCursor);
    const response = await fetch(url, { signal: request.signal });
    if (!response.ok) throw new Error(`Không tải được thư viện (HTTP ${response.status}).`);
    const page = await response.json();
    request.signal.throwIfAborted();
    if (reset) list.replaceChildren();
    for (const video of page.items) renderVideo(video);
    nextCursor = page.next_cursor;
    more.hidden = nextCursor === null;
    status.textContent = list.children.length ? "" : "Thư viện chưa có video.";
  } catch (error) {
    if (!request.signal.aborted) status.textContent = error.message;
  } finally {
    if (activeRequest === request) more.disabled = false;
  }
}

more.addEventListener("click", () => void loadPage(false));
document.addEventListener("video-library-changed", () => void loadPage(true));
void loadPage(true);
