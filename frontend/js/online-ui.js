const byId = id => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const shortDate = new Intl.DateTimeFormat("vi-VN", {dateStyle: "medium"});

export function icon(name) {
  const svg = document.createElementNS(SVG, "svg");
  const use = document.createElementNS(SVG, "use");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

export function notice(target, message, tone = "") {
  target.textContent = message;
  target.dataset.tone = tone;
}

export function badge(message, tone) {
  const label = document.createElement("span");
  label.className = "badge";
  notice(label, message, tone);
  return label;
}

export function videoCard(item, play) {
  const card = document.createElement("li");
  card.className = "video-card";
  card.dataset.title = item.title;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "video-select";
  button.setAttribute("aria-label", "Phát " + item.title);
  button.disabled = item.status !== "READY";
  button.addEventListener("click", play);

  const cover = document.createElement("span");
  cover.className = "card-art";
  cover.setAttribute("aria-hidden", "true");
  const coverIcon = document.createElement("span");
  coverIcon.className = "card-mark";
  coverIcon.append(icon("play"));
  cover.append(coverIcon);
  if (Number.isFinite(item.duration) && item.duration >= 0) {
    const duration = document.createElement("span");
    duration.className = "duration";
    const seconds = Math.floor(item.duration);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds / 60) % 60;
    const last = String(seconds % 60).padStart(2, "0");
    duration.textContent = hours ? `${hours}:${String(minutes).padStart(2, "0")}:${last}` : `${minutes}:${last}`;
    cover.append(duration);
  }
  const title = document.createElement("span");
  title.className = "card-title";
  title.textContent = item.title;
  button.append(cover, title);
  card.append(button);

  const meta = document.createElement("div");
  meta.className = "card-meta";
  if (item.resolution) meta.append(badge(item.resolution));
  if (item.created_at && !Number.isNaN(Date.parse(item.created_at))) {
    const date = document.createElement("time");
    date.dateTime = item.created_at;
    date.textContent = shortDate.format(new Date(item.created_at));
    meta.append(date);
  }
  if (item.status === "READY" || item.status === "DELETING") {
    const state = badge(item.status === "READY" ? "Sẵn sàng" : "Đang xóa", item.status === "READY" ? "success" : "warning");
    state.classList.add("managed-state");
    meta.append(state);
  }
  if (meta.childNodes.length) card.append(meta);
  return card;
}

export function attachShell() {
  const shell = byId("signed-in");
  const sidebar = byId("sidebar");
  const workspace = byId("workspace");
  const navToggle = byId("sidebar-toggle");
  const overlay = byId("drawer-overlay");
  const account = byId("account-menu");
  const search = byId("video-search");
  const video = byId("player-video");
  const narrow = matchMedia("(max-width: 910px)");
  let isAdmin = false;
  let view = "home";
  let libraryMessage = "";
  let libraryTone = "";

  function syncDrawer() {
    const open = narrow.matches && shell.classList.contains("drawer-open");
    overlay.hidden = !open;
    workspace.inert = open;
    navToggle.setAttribute("aria-expanded", String(narrow.matches ? open : !shell.classList.contains("rail-compact")));
  }

  function closeDrawer(refocus = false) {
    const wasOpen = shell.classList.contains("drawer-open");
    shell.classList.remove("drawer-open");
    syncDrawer();
    if (refocus && wasOpen) navToggle.focus();
  }

  function show(next, focus = false) {
    if (!isAdmin && ["upload", "accounts", "manage"].includes(next)) return;
    view = next;
    const browsing = ["home", "library", "watch"].includes(next);
    byId("browse-view").hidden = !browsing;
    byId("library-hero").hidden = next !== "home";
    byId("watch-view").hidden = next !== "watch";
    byId("manage-view").hidden = next !== "manage";
    byId("upload-view").hidden = next !== "upload";
    byId("accounts-view").hidden = next !== "accounts";
    byId("catalog-view").hidden = !browsing && next !== "manage";
    byId("video-library").classList.toggle("management-grid", next === "manage");
    if (next !== "watch" && !video.paused) video.pause();
    const current = next === "watch" ? "library" : next;
    for (const button of sidebar.querySelectorAll("[data-view]")) {
      if (button.dataset.view === current) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    closeDrawer();
    window.scrollTo(0, 0);
    if (focus) {
      const heading = workspace.querySelector(
        {home: "#library-hero h1", library: "#library-heading h2", watch: "#video-title",
          manage: "#manage-view h1", upload: "#upload-view h1", accounts: "#accounts-view h1"}[next]);
      heading.tabIndex = -1;
      heading.focus({preventScroll: true});
    }
  }

  function refreshSearch() {
    const query = search.value.trim().toLocaleLowerCase("vi");
    let total = 0;
    let visible = 0;
    for (const card of byId("video-library").children) {
      total++;
      card.hidden = !card.dataset.title.toLocaleLowerCase("vi").includes(query);
      if (!card.hidden) visible++;
    }
    notice(byId("library-status"), libraryMessage ||
      (query && total && !visible ? "Chưa tìm thấy video đã tải phù hợp." : ""), libraryTone);
  }

  navToggle.addEventListener("click", () => {
    if (narrow.matches) {
      if (shell.classList.contains("drawer-open")) closeDrawer(true);
      else {
        account.open = false;
        shell.classList.add("drawer-open");
        syncDrawer();
        sidebar.querySelector('[aria-current="page"]')?.focus();
      }
    } else {
      shell.classList.toggle("rail-compact");
      syncDrawer();
    }
  });
  narrow.addEventListener("change", () => closeDrawer());
  overlay.addEventListener("click", () => closeDrawer(true));
  shell.addEventListener("click", event => {
    const button = event.target.closest("[data-view]");
    if (button) show(button.dataset.view, true);
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    closeDrawer(true);
    if (account.open) {
      account.open = false;
      account.querySelector("summary").focus();
    }
  });
  document.addEventListener("pointerdown", event => {
    if (!account.contains(event.target)) account.open = false;
  });
  search.addEventListener("input", () => {
    if (!["home", "library", "watch", "manage"].includes(view)) show("library");
    refreshSearch();
  });

  const password = byId("password");
  const toggle = byId("toggle-password");
  function setPasswordVisible(visible) {
    password.type = visible ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(visible));
    toggle.setAttribute("aria-label", visible ? "Ẩn mật khẩu" : "Hiện mật khẩu");
  }
  toggle.addEventListener("click", () => setPasswordVisible(password.type === "password"));

  return {
    login(user) {
      isAdmin = user.role === "ADMIN";
      byId("auth-view").hidden = true;
      shell.hidden = false;
      byId("admin").hidden = !isAdmin;
      byId("admin-nav").hidden = !isAdmin;
      byId("playback-check").hidden = !isAdmin;
      byId("account-avatar").textContent = user.username.slice(0, 1);
      byId("account-name").textContent = user.username;
      byId("account-role").textContent = `${user.username} · ${isAdmin ? "Admin" : "User"}`;
      setPasswordVisible(false);
      notice(byId("account-status"), "");
      show("home", true);
    },
    logout(message) {
      shell.hidden = true;
      byId("auth-view").hidden = false;
      byId("admin").hidden = true;
      byId("admin-nav").hidden = true;
      byId("watch-view").hidden = true;
      byId("video-title").textContent = "";
      account.open = false;
      search.value = "";
      libraryMessage = libraryTone = "";
      isAdmin = false;
      closeDrawer();
      notice(byId("auth-status"), message, message === "Đã đăng xuất." ? "" : "error");
      byId("username").focus();
    },
    watch(title) {
      byId("video-title").textContent = title;
      show("watch", true);
    },
    libraryStatus(message = "", tone = "") {
      libraryMessage = message;
      libraryTone = tone;
      refreshSearch();
    },
  };
}

export function actionDialog() {
  const dialog = byId("action-dialog");
  const field = byId("dialog-field");
  const value = byId("dialog-value");
  const submit = byId("dialog-submit");
  byId("dialog-cancel").addEventListener("click", () => dialog.close("cancel"));
  byId("action-form").addEventListener("submit", event => {
    event.preventDefault();
    dialog.close("ok");
  });

  return {
    ask({title, detail, confirm, initial}) {
      if (dialog.open) return Promise.resolve(null);
      const editing = initial !== undefined;
      byId("dialog-title").textContent = title;
      byId("dialog-detail").textContent = detail || "";
      field.hidden = value.disabled = !editing;
      value.required = editing;
      value.value = initial || "";
      submit.textContent = confirm;
      submit.classList.toggle("destructive", !editing);
      dialog.returnValue = "cancel";
      dialog.showModal();
      (editing ? value : byId("dialog-cancel")).focus();
      return new Promise(resolve => dialog.addEventListener("close", () => {
        resolve(dialog.returnValue === "ok" ? (editing ? value.value : true) : null);
      }, {once: true}));
    },
    close() { if (dialog.open) dialog.close("cancel"); },
  };
}
