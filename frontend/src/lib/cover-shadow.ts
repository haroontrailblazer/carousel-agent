/** Same proportional mask as app/cover_shadow.py, for image and video covers. */
export function coverShadowPixels(width: number, height: number, coverage = 52, blur = 65, curve = 0): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(width * height * 4)
  const top = height * (1 - coverage / 100)
  const fade = Math.min(height * coverage / 100, height * .18 * blur / 65)
  const lift = height * coverage / 100 * .35 * curve / 100
  for (let x = 0; x < width; x++) {
    const edge = (2 * x / Math.max(1, width - 1) - 1) ** 2
    const start = Math.round(top - lift * edge)
    const end = Math.min(height - 1, Math.round(top + fade - lift * edge))
    for (let y = Math.max(0, start); y < height; y++) {
      pixels[(y * width + x) * 4 + 3] = y >= end ? 255
        : Math.round(255 * (y - start) / Math.max(1, end - start))
    }
  }
  return pixels
}
