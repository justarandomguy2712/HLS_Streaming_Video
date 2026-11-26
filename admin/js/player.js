import { MSEPlayer } from "/js/mse-player.js";

const form = document.querySelector("#player-form");
if (form) {
  const input = form.querySelector("#player-id");
  const video = document.querySelector("#player-video");
  const status = document.querySelector("#player-status");
  const showStatus = message => { status.textContent = message; };
  let player;

  const openVideo = async (autoplay = false) => {
    player?.close();
    const session = new MSEPlayer(video, showStatus);
    player = session;
    showStatus("Đang mở playlist…");
    try {
      await session.start(input.value.trim());
      if (autoplay && session === player) {
        try {
          await video.play();
        } catch (error) {
          if (session !== player) return;
          if (error.name === "NotAllowedError") showStatus("Đã nạp video. Bấm Play trên trình phát.");
          else throw error;
        }
      }
    } catch (error) {
      session.fail(error);
    }
  };

  form.addEventListener("submit", event => {
    event.preventDefault();
    void openVideo();
  });
  document.addEventListener("video-ready", event => {
    input.value = event.detail;
    if (!player) showStatus("Đã nhận Video ID. Bấm Mở video để nạp playlist.");
  });
  document.addEventListener("play-library-video", event => {
    input.value = event.detail;
    void openVideo(true);
  });
  const initialId = new URLSearchParams(location.search).get("video");
  if (initialId) {
    input.value = initialId;
    void openVideo();
  }
}
