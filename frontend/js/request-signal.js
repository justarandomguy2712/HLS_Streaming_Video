// Safari trên iOS 17.1 có ManagedMediaSource trước khi có AbortSignal.any().
export function requestSignal(signals, timeout) {
  const controller = new AbortController();
  const listeners = new Map();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      break;
    }
    const abort = () => controller.abort(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    listeners.set(signal, abort);
  }
  const timer = timeout === undefined ? undefined : setTimeout(() => {
    controller.abort(new DOMException("Request timed out.", "TimeoutError"));
  }, timeout);
  return {
    signal: controller.signal,
    close() {
      clearTimeout(timer);
      for (const [signal, abort] of listeners) signal.removeEventListener("abort", abort);
    }
  };
}
