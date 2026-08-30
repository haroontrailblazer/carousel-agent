import * as React from "react"

export type DesignPosition =
  | "top-left"
  | "top-center"
  | "top-right"
  | "middle-left"
  | "middle-center"
  | "middle-right"
  | "bottom-left"
  | "bottom-center"
  | "bottom-right"

export type DesignImageType =
  | "editorial"
  | "product"
  | "illustration"
  | "diagram"
  | "none"

export type DesignFont = "condensed" | "sans" | "serif"

export type SlideDesign = {
  titleSize: number
  titlePosition: DesignPosition
  titleAlign: "left" | "center" | "right"
  fontFamily: DesignFont
  background: string
  textColor: string
  accentColor: string
  safeMargin: number
  imageType: DesignImageType
  imagePosition: DesignPosition
  imageScale: number
}

export type CarouselDesign = {
  id: string
  name: string
  logoVisible: boolean
  logoPosition: DesignPosition
  logoSize: number
  handleVisible: boolean
  handlePosition: DesignPosition
  handleSize: number
  cover: SlideDesign
  inside: SlideDesign
}

const STORAGE_KEY = "carousel-designs:v1"

const baseSlide: SlideDesign = {
  titleSize: 76,
  titlePosition: "top-left",
  titleAlign: "left",
  fontFamily: "condensed",
  background: "#f7f7f5",
  textColor: "#161811",
  accentColor: "#8fb832",
  safeMargin: 88,
  imageType: "editorial",
  imagePosition: "bottom-center",
  imageScale: 100,
}

export const PREBUILT_DESIGNS: CarouselDesign[] = [
  {
    id: "editorial-signal",
    name: "Editorial Signal",
    logoVisible: true,
    logoPosition: "bottom-left",
    logoSize: 56,
    handleVisible: true,
    handlePosition: "bottom-left",
    handleSize: 32,
    cover: {
      ...baseSlide,
      titleSize: 128,
      titlePosition: "bottom-center",
      titleAlign: "center",
      background: "#161811",
      textColor: "#e8e4d6",
    },
    inside: { ...baseSlide },
  },
  {
    id: "minimal-mono",
    name: "Minimal Mono",
    logoVisible: true,
    logoPosition: "top-left",
    logoSize: 48,
    handleVisible: true,
    handlePosition: "top-right",
    handleSize: 28,
    cover: {
      ...baseSlide,
      titleSize: 112,
      titlePosition: "middle-left",
      titleAlign: "left",
      background: "#ffffff",
      textColor: "#111111",
      accentColor: "#111111",
      imageType: "none",
    },
    inside: {
      ...baseSlide,
      titleSize: 68,
      background: "#ffffff",
      textColor: "#111111",
      accentColor: "#111111",
      imageType: "diagram",
      imageScale: 82,
    },
  },
  {
    id: "product-focus",
    name: "Product Focus",
    logoVisible: true,
    logoPosition: "bottom-left",
    logoSize: 52,
    handleVisible: true,
    handlePosition: "bottom-right",
    handleSize: 28,
    cover: {
      ...baseSlide,
      titleSize: 104,
      titlePosition: "bottom-left",
      titleAlign: "left",
      background: "#181818",
      textColor: "#f8f5eb",
      accentColor: "#b8ef43",
      imageType: "product",
      imagePosition: "top-center",
      imageScale: 90,
    },
    inside: {
      ...baseSlide,
      titleSize: 72,
      imageType: "product",
      imagePosition: "middle-right",
      imageScale: 68,
    },
  },
]

function cloneDesign(design: CarouselDesign): CarouselDesign {
  return {
    ...design,
    cover: { ...design.cover },
    inside: { ...design.inside },
  }
}

function readDesigns(): CarouselDesign[] {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (!stored) return PREBUILT_DESIGNS.map(cloneDesign)
    const parsed = JSON.parse(stored)
    if (!Array.isArray(parsed) || parsed.length === 0) {
      return PREBUILT_DESIGNS.map(cloneDesign)
    }
    return parsed as CarouselDesign[]
  } catch {
    return PREBUILT_DESIGNS.map(cloneDesign)
  }
}

export function useCarouselDesigns() {
  const [designs, setDesigns] = React.useState<CarouselDesign[]>(readDesigns)

  React.useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(designs))
  }, [designs])

  return [designs, setDesigns] as const
}

export function duplicateDesign(design: CarouselDesign): CarouselDesign {
  return {
    ...cloneDesign(design),
    id: `design-${crypto.randomUUID()}`,
    name: `${design.name} copy`,
  }
}

export function newDesign(): CarouselDesign {
  return {
    ...cloneDesign(PREBUILT_DESIGNS[0]),
    id: `design-${crypto.randomUUID()}`,
    name: "Untitled design",
  }
}

export function designPayload(design: CarouselDesign) {
  return {
    id: design.id,
    name: design.name,
    logo_visible: design.logoVisible,
    logo_position: design.logoPosition,
    logo_size: design.logoSize,
    handle_visible: design.handleVisible,
    handle_position: design.handlePosition,
    handle_size: design.handleSize,
    cover: {
      title_size: design.cover.titleSize,
      title_position: design.cover.titlePosition,
      title_align: design.cover.titleAlign,
      font_family: design.cover.fontFamily,
      background: design.cover.background,
      text_color: design.cover.textColor,
      accent_color: design.cover.accentColor,
      safe_margin: design.cover.safeMargin,
      image_type: design.cover.imageType,
      image_position: design.cover.imagePosition,
      image_scale: design.cover.imageScale,
    },
    inside: {
      title_size: design.inside.titleSize,
      title_position: design.inside.titlePosition,
      title_align: design.inside.titleAlign,
      font_family: design.inside.fontFamily,
      background: design.inside.background,
      text_color: design.inside.textColor,
      accent_color: design.inside.accentColor,
      safe_margin: design.inside.safeMargin,
      image_type: design.inside.imageType,
      image_position: design.inside.imagePosition,
      image_scale: design.inside.imageScale,
    },
  }
}

