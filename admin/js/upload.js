const form = document.querySelector("#upload-form");
const input = document.querySelector("#video");
const button = form.querySelector("button");
const statusText = document.querySelector("#upload-status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = input.files[0];
  button.disabled = true;
  statusText.textContent = "Đang tải MP4 lên…";

  try {
    const upload = await fetch("/api/videos/upload", {
      method: "POST",
      headers: {
        "Content-Type": "video/mp4",
        "X-Filename": encodeURIComponent(file.name)
      },
      body: file
    });
    const accepted = await upload.json();
    if (!upload.ok) throw new Error(accepted.detail || `HTTP ${upload.status}`);
    document.dispatchEvent(new Event("video-library-changed"));

    const statusUrl = upload.headers.get("Location");
    while (true) {
      const response = await fetch(statusUrl);
      const video = await response.json();
      if (!response.ok) throw new Error(video.detail || `HTTP ${response.status}`);
      const online = video.online_status === "published" ? "Đã có trên thư viện online"
        : video.online_status === "pending" ? "Đang chờ đồng bộ thư viện online" : null;
      statusText.textContent = [video.id, video.status, video.processing_note, online].filter(Boolean).join(" · ");
      if (video.status === "failed") {
        document.dispatchEvent(new Event("video-library-changed"));
        throw new Error(`${video.id} · ${video.error}`);
      }
      if (video.status === "ready") {
        document.dispatchEvent(new CustomEvent("video-ready", { detail: video.id }));
        document.dispatchEvent(new Event("video-library-changed"));
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
