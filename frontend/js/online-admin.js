import { badge, notice } from "./online-ui.js";

export function attachAdmin(api, reloadVideos, dialog) {
  const byId = id => document.getElementById(id);
  const list = byId("user-list");
  const resetForm = byId("reset-form");
  const resetDialog = byId("reset-dialog");
  const status = byId("admin-status");
  const videoStatus = byId("video-admin-status");
  const more = byId("user-more");
  let next, target, generation = 0;

  function button(row, text, action, {danger = false, output = status} = {}) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = danger ? "button danger" : "button";
    node.textContent = text;
    node.addEventListener("click", async () => {
      node.disabled = true;
      try { await action(); }
      catch (error) { notice(output, error.message, "error"); }
      finally { node.disabled = false; }
    });
    row.append(node);
  }

  async function change(path, method, body) {
    await api(path, {method, body});
    notice(status, "Đã cập nhật tài khoản.", "success");
    await load(true);
  }

  async function load(fresh) {
    const current = ++generation;
    const initial = !list.children.length && !status.textContent;
    if (initial) notice(status, "Đang tải tài khoản…");
    more.disabled = true;
    try {
      const data = await api("/api/admin/users" + (!fresh && next ? "?cursor=" + encodeURIComponent(next) : ""));
      if (current !== generation) return;
      if (fresh) list.replaceChildren();
      for (const user of data.items) {
        const row = document.createElement("li");
        row.className = "account-row";
        const identity = document.createElement("div");
        identity.className = "account-ident";
        const avatar = document.createElement("span");
        avatar.className = "avatar";
        avatar.setAttribute("aria-hidden", "true");
        avatar.textContent = user.username.slice(0, 1);
        const name = document.createElement("strong");
        name.textContent = user.username;
        identity.append(avatar, name, badge(user.role), badge(user.is_active ? "Đang hoạt động" : "Đã khóa",
          user.is_active ? "success" : "warning"));
        row.append(identity);
        const path = `/api/admin/users/${user.id}`;
        if (user.role === "USER") {
          const actions = document.createElement("div");
          actions.className = "actions";
          button(actions, user.is_active ? "Khóa" : "Mở khóa", async () => {
            if (user.is_active && !await dialog.ask({
              title: "Khóa tài khoản?", detail: `${user.username} sẽ bị đăng xuất khỏi các phiên đang mở.`,
              confirm: "Khóa tài khoản",
            })) return;
            await change(path, "PATCH", {is_active: !user.is_active});
          });
          button(actions, "Đặt lại mật khẩu", () => {
            target = path + "/reset-password";
            byId("reset-account").textContent = user.username;
            resetDialog.showModal();
            byId("reset-password").focus();
          });
          button(actions, "Xóa User", async () => {
            if (await dialog.ask({title: "Xóa tài khoản?", detail: user.username, confirm: "Xóa User"})) {
              await change(path, "DELETE");
            }
          }, {danger: true});
          row.append(actions);
        }
        list.append(row);
      }
      next = data.next_cursor;
      more.hidden = !next;
      if (initial) notice(status, list.children.length ? "" : "Chưa có tài khoản.");
    } catch (error) {
      if (current === generation) notice(status, error.message, "error");
    } finally {
      if (current === generation) more.disabled = false;
    }
  }

  function clearReset() {
    target = null;
    resetForm.reset();
  }

  byId("user-form").addEventListener("submit", async event => {
    event.preventDefault();
    const username = byId("new-username").value;
    const password = byId("new-password").value;
    byId("new-password").value = "";
    try {
      await change("/api/admin/users", "POST", {username, password});
      event.target.reset();
    } catch (error) { notice(status, error.message, "error"); }
  });
  resetForm.addEventListener("submit", async event => {
    event.preventDefault();
    const path = target;
    const password = byId("reset-password").value;
    clearReset();
    resetDialog.close();
    try { if (path) await change(path, "POST", {password}); }
    catch (error) { notice(status, error.message, "error"); }
  });
  byId("cancel-reset").addEventListener("click", () => resetDialog.close());
  resetDialog.addEventListener("close", clearReset);
  more.addEventListener("click", () => { if (next) void load(false); });

  function videoActions(row, item) {
    const path = `/api/admin/videos/${item.id}`;
    const actions = document.createElement("div");
    actions.className = "actions card-actions";
    if (item.status === "READY") button(actions, "Đổi tiêu đề", async () => {
      const title = await dialog.ask({title: "Đổi tiêu đề video", confirm: "Lưu tiêu đề", initial: item.title});
      if (title === null) return;
      await api(path, {method: "PATCH", body: {title}});
      notice(videoStatus, "Đã đổi tiêu đề video.", "success");
      await reloadVideos();
    }, {output: videoStatus});
    button(actions, item.status === "DELETING" ? "Tiếp tục xóa" : "Xóa video", async () => {
      if (!await dialog.ask({title: "Xóa video?", detail: `${item.title} sẽ bị xóa khỏi thư viện và R2.`,
        confirm: "Xóa video"})) return;
      while (await api(path, {method: "DELETE"})) {
        notice(videoStatus, "Đang xóa từng lô object của video…");
      }
      notice(videoStatus, "Đã xóa video.", "success");
      await reloadVideos();
    }, {danger: true, output: videoStatus});
    row.append(actions);
  }

  return {
    load, videoActions,
    reset() {
      generation++;
      next = null;
      clearReset();
      if (resetDialog.open) resetDialog.close();
      dialog.close();
      list.replaceChildren();
      byId("user-form").reset();
      more.hidden = true;
      notice(status, "");
      notice(videoStatus, "");
    },
  };
}

