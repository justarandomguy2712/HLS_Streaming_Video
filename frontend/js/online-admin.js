export function attachAdmin(api, reloadVideos) {
  const byId = id => document.getElementById(id);
  const list = byId("user-list");
  const resetForm = byId("reset-form");
  const status = byId("admin-status");
  const more = byId("user-more");
  let next, target, generation = 0;

  function button(row, text, action) {
    const node = document.createElement("button");
    node.type = "button";
    node.textContent = text;
    node.addEventListener("click", async () => {
      node.disabled = true;
      try { await action(); }
      catch (error) { status.textContent = error.message; }
      finally { node.disabled = false; }
    });
    row.append(" ", node);
  }

  async function change(path, method, body) {
    await api(path, {method, body});
    status.textContent = "Đã cập nhật tài khoản.";
    await load(true);
  }

  async function load(fresh) {
    const current = ++generation;
    more.disabled = true;
    try {
      const data = await api("/api/admin/users" + (!fresh && next ? "?cursor=" + encodeURIComponent(next) : ""));
      if (current !== generation) return;
      if (fresh) list.replaceChildren();
      for (const user of data.items) {
        const row = document.createElement("li");
        row.textContent = `${user.username} · ${user.role}` + (user.is_active ? "" : " · đã khóa");
        const path = `/api/admin/users/${user.id}`;
        if (user.role === "USER") {
          button(row, user.is_active ? "Khóa" : "Mở khóa", () => change(path, "PATCH", {is_active: !user.is_active}));
          button(row, "Đặt lại mật khẩu", () => {
            target = path + "/reset-password";
            resetForm.hidden = false;
            byId("reset-password").focus();
          });
          button(row, "Xóa User", () => {
            if (window.confirm("Xóa tài khoản " + user.username + "?")) return change(path, "DELETE");
          });
        }
        list.append(row);
      }
      next = data.next_cursor;
      more.hidden = !next;
    } catch (error) {
      if (current === generation) status.textContent = error.message;
    } finally {
      if (current === generation) more.disabled = false;
    }
  }

  function clearReset() {
    target = null;
    resetForm.reset();
    resetForm.hidden = true;
  }

  byId("user-form").addEventListener("submit", async event => {
    event.preventDefault();
    const username = byId("new-username").value;
    const password = byId("new-password").value;
    byId("new-password").value = "";
    try {
      await change("/api/admin/users", "POST", {username, password});
      event.target.reset();
    } catch (error) { status.textContent = error.message; }
  });
  resetForm.addEventListener("submit", async event => {
    event.preventDefault();
    const path = target;
    const password = byId("reset-password").value;
    clearReset();
    try { if (path) await change(path, "POST", {password}); }
    catch (error) { status.textContent = error.message; }
  });
  byId("cancel-reset").addEventListener("click", clearReset);
  more.addEventListener("click", () => { if (next) void load(false); });

  function videoActions(row, item) {
    const path = `/api/admin/videos/${item.id}`;
    if (item.status === "READY") button(row, "Đổi tiêu đề", async () => {
      const title = window.prompt("Tiêu đề video", item.title);
      if (title === null) return;
      await api(path, {method: "PATCH", body: {title}});
      await reloadVideos();
    });
    button(row, item.status === "DELETING" ? "Tiếp tục xóa" : "Xóa video", async () => {
      if (!window.confirm("Xóa dữ liệu R2 của video " + item.title + "?")) return;
      while (await api(path, {method: "DELETE"})) {
        status.textContent = "Đang xóa từng lô object của video…";
      }
      status.textContent = "Đã xóa video.";
      await reloadVideos();
    });
  }

  return {
    load, videoActions,
    reset() {
      generation++;
      next = null;
      clearReset();
      list.replaceChildren();
      byId("user-form").reset();
      more.hidden = true;
      status.textContent = "";
    },
  };
}

