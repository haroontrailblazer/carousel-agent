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

export type ElementTransform = {
  x: number
  y: number
  width: number
  height: number
  locked: boolean
}

export type SlideDesign = {
  logoVisible: boolean
  handleVisible: boolean
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
  titleTransform: ElementTransform
  imageTransform: ElementTransform
  logoTransform: ElementTransform
  handleTransform: ElementTransform
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

function transformForPosition(
  position: DesignPosition,
  width: number,
  height: number,
): ElementTransform {
  const [vertical, horizontal] = position.split("-")
  const x = horizontal === "left" ? 8 : horizontal === "right" ? 92 - width : (100 - width) / 2
  const y = vertical === "top" ? 8 : vertical === "bottom" ? 92 - height : (100 - height) / 2
  return { x, y, width, height, locked: false }
}

const baseSlide: SlideDesign = {
  logoVisible: true,
  handleVisible: true,
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
  titleTransform: transformForPosition("top-left", 76, 20),
  imageTransform: transformForPosition("bottom-center", 84, 32),
  logoTransform: transformForPosition("bottom-left", 6, 5),
  handleTransform: transformForPosition("bottom-left", 28, 5),
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
      handleVisible: false,
      titleSize: 128,
      titlePosition: "bottom-center",
      titleTransform: transformForPosition("bottom-center", 76, 24),
      titleAlign: "center",
      background: "#161811",
      textColor: "#e8e4d6",
      imageTransform: { x: 0, y: 0, width: 100, height: 100, locked: true },
      logoTransform: transformForPosition("bottom-left", 6, 5),
      handleTransform: { x: 16, y: 87, width: 28, height: 5, locked: false },
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
      handleVisible: false,
      titleSize: 112,
      titlePosition: "middle-left",
      titleTransform: transformForPosition("middle-left", 76, 22),
      titleAlign: "left",
      background: "#ffffff",
      textColor: "#111111",
      accentColor: "#111111",
      imageType: "none",
      imageTransform: { x: 0, y: 0, width: 100, height: 100, locked: true },
      logoTransform: transformForPosition("top-left", 6, 5),
      handleTransform: transformForPosition("top-right", 28, 5),
    },
    inside: {
      ...baseSlide,
      titleSize: 68,
      background: "#ffffff",
      textColor: "#111111",
      accentColor: "#111111",
      imageType: "diagram",
      imageScale: 82,
      imageTransform: transformForPosition("bottom-center", 68, 27),
      logoTransform: transformForPosition("top-left", 6, 5),
      handleTransform: transformForPosition("top-right", 28, 5),
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
      handleVisible: false,
      titleSize: 104,
      titlePosition: "bottom-left",
      titleTransform: transformForPosition("bottom-left", 68, 22),
      titleAlign: "left",
      background: "#181818",
      textColor: "#f8f5eb",
      accentColor: "#b8ef43",
      imageType: "product",
      imagePosition: "top-center",
      imageScale: 90,
      imageTransform: { x: 0, y: 0, width: 100, height: 100, locked: true },
      logoTransform: transformForPosition("bottom-left", 6, 5),
      handleTransform: transformForPosition("bottom-right", 28, 5),
    },
    inside: {
      ...baseSlide,
      titleSize: 72,
      imageType: "product",
      imagePosition: "middle-right",
      imageScale: 68,
      imageTransform: transformForPosition("middle-right", 57, 28),
      logoTransform: transformForPosition("bottom-left", 6, 5),
      handleTransform: transformForPosition("bottom-right", 28, 5),
    },
  },
]

function cloneDesign(design: CarouselDesign): CarouselDesign {
  return {
    ...design,
    cover: cloneSlide(design.cover),
    inside: cloneSlide(design.inside),
  }
}

function cloneSlide(slide: SlideDesign): SlideDesign {
  return {
    ...slide,
    titleTransform: { ...slide.titleTransform },
    imageTransform: { ...slide.imageTransform },
    logoTransform: { ...slide.logoTransform },
    handleTransform: { ...slide.handleTransform },
  }
}

function normalizeTransform(
  value: Partial<ElementTransform> | undefined,
  fallback: ElementTransform,
): ElementTransform {
  return {
    x: Number.isFinite(value?.x) ? Number(value?.x) : fallback.x,
    y: Number.isFinite(value?.y) ? Number(value?.y) : fallback.y,
    width: Number.isFinite(value?.width) ? Number(value?.width) : fallback.width,
    height: Number.isFinite(value?.height) ? Number(value?.height) : fallback.height,
    locked: Boolean(value?.locked ?? fallback.locked),
  }
}

function normalizeSlide(
  value: Partial<SlideDesign> | undefined,
  logoPosition: DesignPosition,
  handlePosition: DesignPosition,
  surface: "cover" | "inside",
): SlideDesign {
  const slide = { ...baseSlide, ...value }
  const handleFallback = transformForPosition(handlePosition, 28, 5)
  if (logoPosition === handlePosition) {
    if (handlePosition.endsWith("left")) handleFallback.x = 16
    if (handlePosition.endsWith("right")) handleFallback.x = 64
    if (handlePosition.endsWith("center")) handleFallback.x = 36
  }
  return {
    ...slide,
    logoVisible: value?.logoVisible ?? true,
    handleVisible: value?.handleVisible ?? surface === "inside",
    titleTransform: normalizeTransform(
      value?.titleTransform,
      transformForPosition(slide.titlePosition, 76, 20),
    ),
    imageTransform: normalizeTransform(
      value?.imageTransform,
      transformForPosition(slide.imagePosition, Math.max(30, slide.imageScale * 0.84), 32),
    ),
    logoTransform: normalizeTransform(
      value?.logoTransform,
      transformForPosition(logoPosition, 6, 5),
    ),
    handleTransform: normalizeTransform(
      value?.handleTransform,
      handleFallback,
    ),
  }
}

function normalizeDesign(value: Partial<CarouselDesign>): CarouselDesign {
  const fallback = PREBUILT_DESIGNS[0]
  const logoPosition = value.logoPosition ?? fallback.logoPosition
  const handlePosition = value.handlePosition ?? fallback.handlePosition
  return {
    ...fallback,
    ...value,
    id: value.id || `design-${crypto.randomUUID()}`,
    name: value.name || "Untitled design",
    logoPosition,
    handlePosition,
    cover: normalizeSlide(value.cover, logoPosition, handlePosition, "cover"),
    inside: normalizeSlide(value.inside, logoPosition, handlePosition, "inside"),
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
    return parsed.map((design) => normalizeDesign(design as Partial<CarouselDesign>))
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
      logo_visible: design.cover.logoVisible,
      handle_visible: design.cover.handleVisible,
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
      title_transform: design.cover.titleTransform,
      image_transform: design.cover.imageTransform,
      logo_transform: design.cover.logoTransform,
      handle_transform: design.cover.handleTransform,
    },
    inside: {
      logo_visible: design.inside.logoVisible,
      handle_visible: design.inside.handleVisible,
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
      title_transform: design.inside.titleTransform,
      image_transform: design.inside.imageTransform,
      logo_transform: design.inside.logoTransform,
      handle_transform: design.inside.handleTransform,
    },
  }
}
