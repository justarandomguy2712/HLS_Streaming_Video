// Công cụ chỉ đọc Blob cục bộ từ lab HLSLAB v1. Trình phát không import module này.
const MAGIC = [0x48, 0x4c, 0x53, 0x4c, 0x41, 0x42, 0x1a, 0x0a];
const HEADER_BYTES = 52;
const MAX_INSPECTION_BYTES = 64 * 1024;

export async function readLabHeader(file) {
  const header = await file.slice(0, HEADER_BYTES).arrayBuffer();
  const bytes = new Uint8Array(header);
  if (bytes.length !== HEADER_BYTES || !MAGIC.every((byte, index) => byte === bytes[index])) {
    throw new Error("Header HLSLAB bị cắt hoặc sai magic.");
  }
  const view = new DataView(header);
  const version = view.getUint16(8);
  const flags = view.getUint16(10);
  const payloadLength = view.getBigUint64(12);
  if (version !== 1 || flags !== 0 || payloadLength !== BigInt(file.size - HEADER_BYTES)) {
    throw new Error("Phiên bản, cờ hoặc độ dài HLSLAB không hợp lệ.");
  }
  const digest = new Uint8Array(header.slice(20, HEADER_BYTES));
  const sha256 = Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
  return { version, flags, payloadOffset: HEADER_BYTES, payloadLength, sha256 };
}

export async function readLabBytes(file, offset, count) {
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(count) ||
      count < 0 || count > MAX_INSPECTION_BYTES || count > file.size - offset) {
    throw new RangeError("Vùng quan sát tối đa 64 KiB và phải nằm trong tệp.");
  }
  return new Uint8Array(await file.slice(offset, offset + count).arrayBuffer());
}
