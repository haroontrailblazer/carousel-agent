import manifest from "../../public/fonts/carousel/manifest.json"

// Shared with the Python compositor. These families use bundled files only.
export const DESIGN_FONTS = manifest
export type DesignFontFamily = keyof typeof DESIGN_FONTS
export const designFontFamily = (family: DesignFontFamily) => `"${DESIGN_FONTS[family].family}"`
