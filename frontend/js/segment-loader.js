import { requestSignal } from "./request-signal.js";

const RETRY_DELAYS_MS = [400, 800];

class HTTPError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

function waitBeforeRetry(milliseconds, signal) {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function fetchResource(url, format, signal, requestOptions) {
  const name = new URL(url).pathname.split("/").at(-1);
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    const scope = requestSignal([signal], 20_000);
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        ...requestOptions?.(url),
        signal: scope.signal
      });
      if (!response.ok) {
        response.body?.cancel().catch(() => {});
        throw new HTTPError(response.status);
      }
      return { data: await response[format](), url: response.url || url };
    } catch (error) {
      signal.throwIfAborted();
      const retryable = error instanceof TypeError || error.name === "TimeoutError" ||
        error.status === 408 || error.status === 429 || error.status >= 500;
      if (!retryable || attempt === RETRY_DELAYS_MS.length) {
        const reason = error instanceof HTTPError ? error.message : "lỗi mạng, CORS hoặc phản hồi không hợp lệ";
        const failure = new Error(`Không tải được ${name} (${reason}).`, { cause: error });
        failure.status = error.status;
        throw failure;
      }
    } finally {
      scope.close();
    }
    await waitBeforeRetry(RETRY_DELAYS_MS[attempt], signal);
  }
}

export async function loadSegment(url, signal, requestOptions) {
  const { data } = await fetchResource(url, "arrayBuffer", signal, requestOptions);
  // View trên byte của tên box để phát hiện body sai định dạng, không sao chép segment.
  const type = data.byteLength >= 8
    ? String.fromCharCode(...new Uint8Array(data, 4, 4))
    : "";
  if (!["ftyp", "styp", "sidx", "moof", "free", "skip", "emsg", "prft"].includes(type)) {
    throw new Error("Dữ liệu tải về không có header box fMP4 hợp lệ.");
  }
  return data;
}
