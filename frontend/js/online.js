import { WORKER_ENDPOINT } from "./config.js";
import { MSEPlayer } from "./mse-player.js";
import { attachAdmin } from "./online-admin.js";
import { requestSignal } from "./request-signal.js";

const byId = id => document.getElementById(id);
const form = byId("login-form");
const videos = byId("video-library");
const video = byId("player-video");
const playerStatus = byId("player-status");
const libraryStatus = byId("library-status");
const more = byId("library-more");
const adminPanel = byId("admin");
let session, player, currentVideo, readyToConfirm = false, observedPlay = false, seekFrom = null;
let loginPending = false;

function origin() {
  if (!WORKER_ENDPOINT) throw new Error("Chưa cấu hình Worker trong js/config.js.");
  const url = new URL(WORKER_ENDPOINT);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(local && url.protocol === "http:")) || url.username ||
      url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("URL Worker cần là origin HTTPS.");
  }
  return url.origin;
}

export async function api(path, {access = session, method = "GET", body} = {}) {
  const endpoint = origin();
  const url = new URL(path, endpoint);
  if (url.origin !== endpoint) throw new Error("API nằm ngoài Worker cấu hình.");
  const scope = requestSignal(access ? [access.abort.signal] : [], 25_000);
  let response, data;
  try {
    response = await fetch(url, {
      method, redirect: "error", credentials: "omit",
      headers: {
        ...(access ? {Authorization: `Bearer ${access.token}`} : {}),
        ...(body === undefined ? {} : {"Content-Type": "application/json"}),
      },
      signal: scope.signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    data = response.status === 204 ? null : await response.json();
  } finally {
    scope.close();
  }
  if (!response.ok) {
    if (response.status === 401 && access && access === session) closeSession("Phiên hết hạn. Đăng nhập lại.");
    throw new Error(data?.detail || `HTTP ${response.status}`);
  }
  return data;
}

function closeSession(message = "") {
  const previous = session;
  session = null;
  previous?.abort.abort();
  clearTimeout(previous?.timer);
  player?.close();
  player = null;
  currentVideo = null;
  if (!adminPanel.hidden) admin.reset();
  form.hidden = false;
  byId("signed-in").hidden = true;
  adminPanel.hidden = true;
  byId("auth-status").textContent = message;
  libraryStatus.textContent = "";
  playerStatus.textContent = "";
  videos.replaceChildren();
  more.hidden = true;
}

function updateCheck() {
  byId("playback-check").disabled = !session || session.user.role !== "ADMIN" || !readyToConfirm || !currentVideo;
}

async function openVideo(id, access) {
  if (access !== session) return;
  player?.close();
  currentVideo = id;
  readyToConfirm = observedPlay = false;
  seekFrom = null;
  updateCheck();
  const endpoint = origin();
  const next = new MSEPlayer(video, message => { playerStatus.textContent = message; }, {
    endpoint: identifier => new URL(`/api/videos/${identifier}`, endpoint).href,
    requestOptions(url) {
      if (new URL(url).origin !== endpoint) throw new Error("URL media nằm ngoài Worker.");
      return {headers: {Authorization: `Bearer ${access.token}`}, credentials: "omit", redirect: "error"};
    },
    onUnauthorized() {
      if (session === access) closeSession("Phiên hết hạn hoặc bị thu hồi. Đăng nhập lại.");
    },
  });
  player = next;
  playerStatus.textContent = "Đang mở video…";
  try {
    await next.start(id);
    if (next === player) await video.play();
  } catch (error) {
    if (next !== player) return;
    if (error.name === "NotAllowedError") playerStatus.textContent = "Bấm Play trên trình phát.";
    else next.fail(error);
  }
}

video.addEventListener("playing", () => { observedPlay = true; seekFrom = video.currentTime; });
video.addEventListener("timeupdate", () => {
  if (!video.seeking && observedPlay) seekFrom = video.currentTime;
});
video.addEventListener("seeking", () => {
  if (observedPlay && seekFrom !== null && Math.abs(video.currentTime - seekFrom) >= 0.1) {
    video.dataset.seekingAfterPlay = "true";
  }
});
video.addEventListener("seeked", () => {
  if (observedPlay && video.readyState >= 2 && video.dataset.seekingAfterPlay === "true") {
    readyToConfirm = true;
    updateCheck();
  }
  delete video.dataset.seekingAfterPlay;
});
byId("playback-check").addEventListener("click", async () => {
  if (!readyToConfirm || !currentVideo || session?.user.role !== "ADMIN") return;
  const id = currentVideo;
  if (!window.confirm("Xác nhận video này đã phát và tua thành công từ Worker?")) return;
  try {
    await api(`/api/admin/videos/${id}/playback-check`, {method: "POST", body: {confirmed: true}});
    playerStatus.textContent = "Đã lưu kết quả kiểm tra phát và tua.";
  } catch (error) {
    playerStatus.textContent = error.message;
  }
});

let loadSequence = 0;
async function loadVideos(reset = false) {
  const access = session;
  if (!access) return;
  const sequence = ++loadSequence;
  const cursor = reset ? null : access.cursor;
  more.disabled = true;
  try {
    const path = access.user.role === "ADMIN" ? "/api/admin/videos" : "/api/videos";
    const data = await api(path + (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""), {access});
    if (access !== session || sequence !== loadSequence) return;
    if (reset) videos.replaceChildren();
    for (const item of data.items) {
      const row = document.createElement("li");
      const play = document.createElement("button");
      play.type = "button";
      play.textContent = "Phát";
      play.setAttribute("aria-label", "Phát " + item.title);
      play.disabled = item.status !== "READY";
      play.addEventListener("click", () => void openVideo(item.id, access));
      row.append(item.title + " ", play);
      if (access.user.role === "ADMIN") admin.videoActions(row, item);
      videos.append(row);
    }
    access.cursor = data.next_cursor;
    more.hidden = !access.cursor;
    libraryStatus.textContent = videos.children.length ? "" : "Thư viện chưa có video.";
  } catch (error) {
    if (access === session) libraryStatus.textContent = error.message;
  } finally {
    if (access === session && sequence === loadSequence) more.disabled = false;
  }
}

const admin = attachAdmin(api, () => loadVideos(true));

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (loginPending) return;
  loginPending = true;
  const username = byId("username").value;
  const password = byId("password").value;
  byId("password").value = "";
  try {
    const data = await api("/api/auth/login", {access: null, method: "POST", body: {username, password}});
    session = {...data, abort: new AbortController()};
    session.timer = setTimeout(() => closeSession("Phiên hết hạn. Đăng nhập lại."),
      Math.max(0, data.expires_at * 1000 - Date.now()));
    form.hidden = true;
    byId("signed-in").hidden = false;
    adminPanel.hidden = data.user.role !== "ADMIN";
    byId("auth-status").textContent = data.user.username + " · " + data.user.role;
    updateCheck();
    void loadVideos(true);
    if (!adminPanel.hidden) void admin.load(true);
  } catch (error) {
    byId("auth-status").textContent = error.message;
  } finally {
    loginPending = false;
  }
});
byId("logout").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", {method: "POST"});
    closeSession("Đã đăng xuất.");
  } catch (error) {
    if (session) byId("auth-status").textContent = "Chưa thu hồi được phiên. Hãy thử lại. " + error.message;
  }
});
more.addEventListener("click", () => { if (session?.cursor) void loadVideos(); });

