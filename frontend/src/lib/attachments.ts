/**
 * Files sent with a chat message: what the composer accepts, and how a file becomes the base64 the chat request carries.
 */
import type { ChatAttachment } from "../design-system/types";

/** The media types the composer accepts. */
export const ATTACHMENT_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"];
/** Most files with one message. */
export const ATTACHMENTS_MAX = 3;
/** Largest single file, in bytes. */
export const ATTACHMENT_MAX_BYTES = 4 * 1024 * 1024;
/** Largest total of one message's files, in bytes. */
export const ATTACHMENTS_TOTAL_MAX_BYTES = 8 * 1024 * 1024;

export type AttachmentRefusal =
  | { reason: "type"; name: string }
  | { reason: "size"; name: string }
  | { reason: "count" }
  | { reason: "total" };

/**
 * Adds `incoming` to the files already chosen. A file over a limit is left out; the first one left out is the refusal
 * returned, so the composer can say why.
 */
export function addAttachments(current: File[], incoming: File[]): { files: File[]; refusal: AttachmentRefusal | null } {
  const files = [...current];
  let total = files.reduce((sum, file) => sum + file.size, 0);
  let refusal: AttachmentRefusal | null = null;
  for (const file of incoming) {
    let refused: AttachmentRefusal | null = null;
    if (!ATTACHMENT_TYPES.includes(file.type)) refused = { reason: "type", name: file.name };
    else if (file.size > ATTACHMENT_MAX_BYTES) refused = { reason: "size", name: file.name };
    else if (files.length >= ATTACHMENTS_MAX) refused = { reason: "count" };
    else if (total + file.size > ATTACHMENTS_TOTAL_MAX_BYTES) refused = { reason: "total" };
    if (refused) {
      refusal = refusal ?? refused;
      continue;
    }
    files.push(file);
    total += file.size;
  }
  return { files, refusal };
}

/** "820 KB", "1.4 MB". */
export function fileSizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function base64Of(bytes: Uint8Array): string {
  let binary = "";
  // In pieces: one call with every byte as an argument overflows the stack on a large file.
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/** Reads the files into what the chat request carries. Rejects when a file cannot be read. */
export async function readAttachments(files: File[]): Promise<ChatAttachment[]> {
  return Promise.all(
    files.map(async (file) => ({
      name: file.name,
      media_type: file.type,
      data: base64Of(new Uint8Array(await file.arrayBuffer())),
    })),
  );
}
