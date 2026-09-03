// Getting a phone camera photo down to something that will actually upload.
//
// A photo straight off a modern handset is 3-8MB. Ten of those is 80MB of
// IndexedDB and, more to the point, an upload that will not finish over a
// weak mobile connection from a plant room. Downscaling is not a nicety here;
// it is the difference between a queue that drains and one that does not.
//
// ~1600px on the longest edge at quality 0.72 lands around 200-400KB, which
// is plenty to show a dispatcher what a corroded coil looks like.

/** Longest edge after downscaling. Enough detail to be evidence. */
const MAX_EDGE = 1600;
const QUALITY = 0.72;

export interface Downscaled {
  /** Base64 JPEG, no `data:` prefix -- the shape the API takes. */
  base64: string;
  /** For the thumbnail on the form. Revoke it when done. */
  previewUrl: string;
  bytes: number;
}

/**
 * Downscale and re-encode as JPEG.
 *
 * `imageOrientation: "from-image"` is the part that is easy to miss. Phone
 * cameras write the sensor's raw orientation and an EXIF tag saying how to
 * rotate it; a canvas draw ignores the tag by default, so every photo taken
 * in portrait arrives at the dispatcher on its side. Asking createImageBitmap
 * to apply the tag fixes it once, here, rather than with a CSS transform that
 * only corrects what the technician sees.
 */
export async function downscale(file: File): Promise<Downscaled> {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });

  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d canvas context");
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", QUALITY),
  );
  if (!blob) throw new Error("could not encode the photo");

  return {
    base64: await toBase64(blob),
    previewUrl: URL.createObjectURL(blob),
    bytes: blob.size,
  };
}

/**
 * Blob -> base64, without a FileReader.
 *
 * `btoa` needs a binary string, and building one with `String.fromCharCode`
 * applied to the whole array at once blows the argument limit on anything
 * over about 100KB -- which every photo is. Chunking is what makes this work
 * on real files rather than only on test fixtures.
 */
async function toBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const CHUNK = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** "312 KB". For telling the technician what they are about to queue. */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
