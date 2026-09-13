export type LogoCrop = { x: number; y: number; zoom: number }
export const INITIAL_LOGO_CROP: LogoCrop = { x: 0.5, y: 0.5, zoom: 1 }

/** Decode locally, including the browser's EXIF orientation handling. */
export async function loadDesignLogo(file: File): Promise<HTMLImageElement> {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
    throw new Error("Choose a PNG, JPG or WebP image.")
  }
  if (file.size > 5 * 1024 * 1024) throw new Error("Choose an image smaller than 5 MB.")
  const url = URL.createObjectURL(file)
  try {
    const image = new Image()
    image.src = url
    await image.decode()
    if (image.naturalWidth * image.naturalHeight > 16_000_000) throw new Error("Choose an image smaller than 16 megapixels.")
    return image
  } catch (error) {
    if (error instanceof DOMException) throw new Error("That image could not be opened. Try a PNG, JPG or WebP.")
    throw error
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** Keep the circular window inside the source, even after zooming out. */
export function constrainLogoCrop(image: HTMLImageElement, crop: LogoCrop): LogoCrop {
  const zoom = Math.max(1, Math.min(4, crop.zoom))
  const edge = Math.min(image.naturalWidth, image.naturalHeight) / zoom
  const halfX = edge / (2 * image.naturalWidth)
  const halfY = edge / (2 * image.naturalHeight)
  return { zoom, x: Math.max(halfX, Math.min(1 - halfX, crop.x)), y: Math.max(halfY, Math.min(1 - halfY, crop.y)) }
}

/** One geometry and alpha mask for the crop dialog, previews and saved logo. */
export function drawDesignLogo(canvas: HTMLCanvasElement, image: HTMLImageElement, crop: LogoCrop, side: number, background = "") {
  const context = canvas.getContext("2d")
  if (!context) throw new Error("Your browser could not prepare this image.")
  const { x, y, zoom } = constrainLogoCrop(image, crop)
  const edge = Math.min(image.naturalWidth, image.naturalHeight) / zoom
  canvas.width = side
  canvas.height = side
  context.imageSmoothingQuality = "high"
  context.save()
  context.beginPath()
  context.arc(side / 2, side / 2, side / 2, 0, Math.PI * 2)
  context.clip()
  if (background) { context.fillStyle = background; context.fillRect(0, 0, side, side) }
  context.drawImage(image, x * image.naturalWidth - edge / 2, y * image.naturalHeight - edge / 2, edge, edge, 0, 0, side, side)
  context.restore()
}

/** Store the actual circular pixels; renderers preserve the transparent corners. */
export function prepareDesignLogo(image: HTMLImageElement, crop: LogoCrop): string {
  const canvas = document.createElement("canvas")
  for (const side of [512, 384, 256, 192]) {
    drawDesignLogo(canvas, image, crop, side)
    const lossless = canvas.toDataURL("image/png")
    if (lossless.length <= 65_550) return lossless
    const data = canvas.toDataURL("image/webp", 0.9)
    if (data.length <= 65_550) return data
  }
  throw new Error("This image is too detailed for a logo. Try a simpler image.")
}
