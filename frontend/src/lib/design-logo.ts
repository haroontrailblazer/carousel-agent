/** Normalize uploads to small, portable logo snapshots; keep transparency and aspect ratio. */
export async function prepareDesignLogo(file: File): Promise<string> {
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
    const canvas = document.createElement("canvas")
    const context = canvas.getContext("2d")
    if (!context) throw new Error("Your browser could not prepare this image.")
    for (const side of [512, 384, 256, 192]) {
      const ratio = Math.min(1, side / Math.max(image.naturalWidth, image.naturalHeight))
      canvas.width = Math.max(1, Math.round(image.naturalWidth * ratio))
      canvas.height = Math.max(1, Math.round(image.naturalHeight * ratio))
      context.drawImage(image, 0, 0, canvas.width, canvas.height)
      const data = canvas.toDataURL("image/webp", 0.85)
      if (data.length <= 65_550) return data
    }
    throw new Error("This image is too detailed for a logo. Try a smaller image.")
  } catch (error) {
    if (error instanceof DOMException) throw new Error("That image could not be opened. Try a PNG, JPG or WebP.")
    throw error
  } finally {
    URL.revokeObjectURL(url)
  }
}
