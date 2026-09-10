import * as React from "react"

import { get, put } from "@/lib/api"

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
  shadowVisible: boolean
  shadowOpacity: number
  shadowHeight: number
  shadowSoftness: number
  shadowColor: string
  titleSize: number
  titlePosition: DesignPosition
  titleAlign: "left" | "center" | "right"
  fontFamily: DesignFont
  background: string
  textColor: string
  highlightTextColor: string
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
  handleText?: string
  logoDataUrl?: string
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

const FULL_BLEED_COVER_TRANSFORM: ElementTransform = {
  x: 0,
  y: 0,
  width: 100,
  height: 100,
  locked: true,
}

function readableCoverColor(color: string): string {
  const rgb = [1, 3, 5].map(start => parseInt(color.slice(start, start + 2), 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] < 0.175 ? "#F6F4F0" : color
}

// Covers always use source media and a black fade; inside slides remain freeform.
export function coverLayout(slide: SlideDesign): SlideDesign {
  const title = slide.titleTransform
  const height = Math.min(title?.height ?? 24, 24)
  return {
    ...slide,
    textColor: title?.y < 62 ? readableCoverColor(slide.textColor) : slide.textColor,
    highlightTextColor: title?.y < 62 ? readableCoverColor(slide.highlightTextColor) : slide.highlightTextColor,
    imageType: "editorial", imagePosition: "middle-center", imageScale: 100,
    imageTransform: { ...FULL_BLEED_COVER_TRANSFORM },
    shadowVisible: true, shadowColor: "#000000", shadowOpacity: 100,
    shadowHeight: 52, shadowSoftness: 65,
    titlePosition: ("bottom-" + slide.titleAlign) as DesignPosition,
    titleTransform: { ...title, height, y: Math.max(62, Math.min(title?.y ?? 62, 86 - height)) },
  }
}

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
  shadowVisible: false,
  shadowOpacity: 64,
  shadowHeight: 44,
  shadowSoftness: 36,
  shadowColor: "#000000",
  titleSize: 76,
  titlePosition: "top-left",
  titleAlign: "left",
  fontFamily: "condensed",
  background: "#f7f7f5",
  textColor: "#161811",
  highlightTextColor: "#8fb832",
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

function studioDesign(dark: boolean): CarouselDesign {
  const colors = dark
    ? { background: "#000000", textColor: "#F6F4F0", highlightTextColor: "#F79270", accentColor: "#F79270" }
    : { background: "#F6F4F0", textColor: "#252420", highlightTextColor: "#C74726", accentColor: "#C74726" }
  const shared: SlideDesign = {
    ...baseSlide, ...colors, fontFamily: "sans", titleAlign: "left",
    titlePosition: "top-left", titleSize: 104,
    logoTransform: { x: 8, y: 88, width: 6, height: 5, locked: false },
    handleTransform: { x: 17, y: 88, width: 32, height: 5, locked: false },
    titleTransform: { x: 8, y: 12, width: 84, height: 32, locked: false },
  }
  return {
    id: dark ? "studio-black" : "studio-light",
    name: dark ? "Studio Black" : "Studio Light",
    logoVisible: true, logoPosition: "bottom-left", logoSize: 64,
    handleVisible: true, handlePosition: "bottom-left", handleSize: 30,
    cover: { ...shared, titleSize: 112, imageType: "product",
      imagePosition: "middle-center", imageScale: 100, imageTransform: { ...FULL_BLEED_COVER_TRANSFORM } },
    inside: { ...shared, titleSize: 88, imageType: "product",
      imageTransform: { x: 8, y: 47, width: 84, height: 36, locked: false } },
  }
}

const ORIGINAL_DESIGNS: CarouselDesign[] = [
  studioDesign(false),
  studioDesign(true),
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
      shadowVisible: true,
      shadowOpacity: 72,
      shadowHeight: 48,
      shadowSoftness: 42,
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
      shadowVisible: false,
      titleSize: 112,
      titlePosition: "middle-left",
      titleTransform: transformForPosition("middle-left", 76, 22),
      titleAlign: "left",
      background: "#ffffff",
      textColor: "#111111",
      highlightTextColor: "#111111",
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
      highlightTextColor: "#111111",
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
      shadowVisible: true,
      shadowOpacity: 78,
      shadowHeight: 52,
      shadowSoftness: 32,
      titleSize: 104,
      titlePosition: "bottom-left",
      titleTransform: transformForPosition("bottom-left", 68, 22),
      titleAlign: "left",
      background: "#181818",
      textColor: "#f8f5eb",
      highlightTextColor: "#b8ef43",
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
  {
    id: "newsroom-grid",
    name: "Newsroom Grid",
    logoVisible: true,
    logoPosition: "top-left",
    logoSize: 48,
    handleVisible: true,
    handlePosition: "bottom-right",
    handleSize: 26,
    cover: {
      ...baseSlide,
      handleVisible: false,
      shadowVisible: true,
      shadowOpacity: 68,
      shadowHeight: 42,
      shadowSoftness: 52,
      titleSize: 96,
      titlePosition: "bottom-left",
      titleTransform: transformForPosition("bottom-left", 70, 23),
      titleAlign: "left",
      background: "#151814",
      textColor: "#f1eee4",
      highlightTextColor: "#b8ef43",
      accentColor: "#b8ef43",
      imageType: "editorial",
      imageTransform: { x: 0, y: 0, width: 100, height: 100, locked: true },
      logoTransform: transformForPosition("top-left", 6, 5),
      handleTransform: transformForPosition("bottom-right", 28, 5),
    },
    inside: {
      ...baseSlide,
      titleSize: 66,
      titleTransform: transformForPosition("top-left", 62, 18),
      imageType: "editorial",
      imageScale: 86,
      imageTransform: transformForPosition("bottom-center", 84, 36),
      background: "#eeece4",
      textColor: "#161811",
      logoTransform: transformForPosition("top-left", 6, 5),
      handleTransform: transformForPosition("bottom-right", 28, 5),
    },
  },
  {
    id: "bold-type",
    name: "Bold Type",
    logoVisible: true,
    logoPosition: "bottom-left",
    logoSize: 48,
    handleVisible: true,
    handlePosition: "bottom-right",
    handleSize: 28,
    cover: {
      ...baseSlide,
      handleVisible: false,
      shadowVisible: false,
      titleSize: 144,
      titlePosition: "middle-left",
      titleTransform: transformForPosition("middle-left", 82, 38),
      titleAlign: "left",
      background: "#b8ef43",
      textColor: "#10130d",
      highlightTextColor: "#10130d",
      accentColor: "#10130d",
      imageType: "none",
      imageTransform: { x: 0, y: 0, width: 100, height: 100, locked: true },
      logoTransform: transformForPosition("bottom-left", 6, 5),
      handleTransform: transformForPosition("bottom-right", 28, 5),
    },
    inside: {
      ...baseSlide,
      titleSize: 82,
      imageType: "illustration",
      imageScale: 72,
      imageTransform: transformForPosition("bottom-right", 62, 34),
      background: "#10130d",
      textColor: "#f4f1e7",
      highlightTextColor: "#b8ef43",
      accentColor: "#b8ef43",
      logoTransform: transformForPosition("bottom-left", 6, 5),
      handleTransform: transformForPosition("bottom-right", 28, 5),
    },
  },
]

// Keep the original definitions for an exact migration of untouched starter
// designs. A customized saved design is never replaced by a refreshed preset.
const PREVIOUS_PREBUILT_DESIGNS: CarouselDesign[] = ORIGINAL_DESIGNS.map(original => {
  const design = cloneDesign(original)
  if (design.id.startsWith("studio-")) return design
  const photo = design.id === "editorial-signal" || design.id === "newsroom-grid"
  const mono = design.id === "minimal-mono"
  const bold = design.id === "bold-type"
  design.cover = {
    ...design.cover,
    background: mono ? "#F6F4F0" : bold ? "#C74726" : "#161616",
    textColor: mono ? "#252420" : "#F6F4F0",
    highlightTextColor: mono ? "#252420" : bold ? "#F6F4F0" : "#F79270",
    accentColor: mono ? "#252420" : bold ? "#F6F4F0" : "#F79270",
    fontFamily: design.id === "editorial-signal" || mono ? "serif" : bold ? "condensed" : "sans",
    titleSize: bold ? 120 : photo ? 100 : 104,
    titleTransform: { x: 8, y: photo ? 65 : 12, width: 84, height: 26, locked: false },
    imageType: photo ? "editorial" : bold ? "illustration" : "product",
    imageScale: 100, imagePosition: "middle-center",
    imageTransform: { ...FULL_BLEED_COVER_TRANSFORM },
    shadowVisible: photo,
    logoTransform: { x: 8, y: photo ? 7 : 88, width: 6, height: 5, locked: false },
  }
  design.inside = {
    ...design.inside,
    background: bold ? "#000000" : "#F6F4F0",
    textColor: bold ? "#F6F4F0" : "#252420",
    highlightTextColor: mono ? "#252420" : bold ? "#F79270" : "#C74726",
    accentColor: mono ? "#252420" : bold ? "#F79270" : "#C74726",
    fontFamily: design.cover.fontFamily, titleSize: 80,
    titleTransform: { x: 8, y: 12, width: 84, height: 30, locked: false },
    imageType: design.cover.imageType,
    imageTransform: { x: 8, y: 47, width: 84, height: 36, locked: false },
    logoTransform: { x: 8, y: 88, width: 6, height: 5, locked: false },
    handleTransform: { x: 17, y: 88, width: 32, height: 5, locked: false },
  }
  return design
})

export const PREBUILT_DESIGNS: CarouselDesign[] = PREVIOUS_PREBUILT_DESIGNS.map(design => ({
  ...design,
  cover: coverLayout({
    ...design.cover, textColor: "#F6F4F0",
    highlightTextColor: design.id === "minimal-mono" ? "#F6F4F0" : "#F79270",
    titleSize: 100, titleTransform: { x: 8, y: 62, width: 84, height: 24, locked: false },
    logoVisible: true, handleVisible: true,
    logoTransform: { x: 8, y: 90, width: 6, height: 5, locked: false },
    handleTransform: { x: 17, y: 90, width: 68, height: 5, locked: false },
  }),
}))

function refreshUntouchedPreset(design: CarouselDesign): CarouselDesign {
  const previous = [...ORIGINAL_DESIGNS, ...PREVIOUS_PREBUILT_DESIGNS].filter(item => item.id === design.id)
  if (!previous.some(item => JSON.stringify(designPayload(design)) === JSON.stringify(designPayload(normalizeDesign(item))))) return design
  return cloneDesign(PREBUILT_DESIGNS.find(item => item.id === design.id)!)
}

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
    shadowVisible: value?.shadowVisible ?? (surface === "cover" && slide.imageType !== "none"),
    shadowOpacity: Number.isFinite(value?.shadowOpacity)
      ? Number(value?.shadowOpacity)
      : surface === "cover"
        ? 72
        : baseSlide.shadowOpacity,
    shadowHeight: Number.isFinite(value?.shadowHeight)
      ? Number(value?.shadowHeight)
      : surface === "cover"
        ? 48
        : baseSlide.shadowHeight,
    shadowSoftness: Number.isFinite(value?.shadowSoftness)
      ? Number(value?.shadowSoftness)
      : surface === "cover"
        ? 42
        : baseSlide.shadowSoftness,
    shadowColor: value?.shadowColor || baseSlide.shadowColor,
    imagePosition: surface === "cover" ? "middle-center" : slide.imagePosition,
    imageScale: surface === "cover" ? 100 : slide.imageScale,
    highlightTextColor: value?.highlightTextColor || value?.accentColor || baseSlide.highlightTextColor,
    titleTransform: normalizeTransform(
      value?.titleTransform,
      transformForPosition(slide.titlePosition, 76, 20),
    ),
    // Cover media is not a movable image slot. It is the source clip/poster
    // cropped across the complete 4:5 canvas. This deliberately migrates old
    // browser and Supabase designs whose cover image used a small bottom box.
    imageTransform: surface === "cover"
      ? { ...FULL_BLEED_COVER_TRANSFORM }
      : normalizeTransform(
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

type DesignInput = Partial<Omit<CarouselDesign, "cover" | "inside">> & {
  cover?: Partial<SlideDesign>
  inside?: Partial<SlideDesign>
}

function normalizeDesign(value: DesignInput): CarouselDesign {
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
    cover: coverLayout(normalizeSlide(value.cover, logoPosition, handlePosition, "cover")),
    inside: normalizeSlide(value.inside, logoPosition, handlePosition, "inside"),
  }
}

type PersistedSlideDesign = {
  logo_visible?: boolean
  handle_visible?: boolean
  shadow_visible?: boolean
  shadow_opacity?: number
  shadow_height?: number
  shadow_softness?: number
  shadow_color?: string
  title_size?: number
  title_position?: DesignPosition
  title_align?: SlideDesign["titleAlign"]
  font_family?: DesignFont
  background?: string
  text_color?: string
  highlight_text_color?: string
  accent_color?: string
  safe_margin?: number
  image_type?: DesignImageType
  image_position?: DesignPosition
  image_scale?: number
  title_transform?: ElementTransform
  image_transform?: ElementTransform
  logo_transform?: ElementTransform
  handle_transform?: ElementTransform
}

type PersistedCarouselDesign = {
  id: string
  name: string
  handle_text?: string
  logo_data_url?: string
  logo_visible?: boolean
  logo_position?: DesignPosition
  logo_size?: number
  handle_visible?: boolean
  handle_position?: DesignPosition
  handle_size?: number
  cover?: PersistedSlideDesign
  inside?: PersistedSlideDesign
}

type DesignLibraryResponse = { items: PersistedCarouselDesign[] }
export type DesignSyncStatus = "loading" | "saving" | "synced" | "offline"

function fromPersistedSlide(value: PersistedSlideDesign | undefined): Partial<SlideDesign> {
  if (!value) return {}
  const mapped: Partial<SlideDesign> = {
    logoVisible: value.logo_visible,
    handleVisible: value.handle_visible,
    shadowVisible: value.shadow_visible,
    shadowOpacity: value.shadow_opacity,
    shadowHeight: value.shadow_height,
    shadowSoftness: value.shadow_softness,
    shadowColor: value.shadow_color,
    titleSize: value.title_size,
    titlePosition: value.title_position,
    titleAlign: value.title_align,
    fontFamily: value.font_family,
    background: value.background,
    textColor: value.text_color,
    highlightTextColor: value.highlight_text_color,
    accentColor: value.accent_color,
    safeMargin: value.safe_margin,
    imageType: value.image_type,
    imagePosition: value.image_position,
    imageScale: value.image_scale,
    titleTransform: value.title_transform,
    imageTransform: value.image_transform,
    logoTransform: value.logo_transform,
    handleTransform: value.handle_transform,
  }
  return Object.fromEntries(
    Object.entries(mapped).filter(([, fieldValue]) => fieldValue !== undefined),
  ) as Partial<SlideDesign>
}

function fromPersistedDesign(value: PersistedCarouselDesign): CarouselDesign {
  const mapped: DesignInput = {
    id: value.id,
    name: value.name,
    handleText: value.handle_text ?? "",
    logoDataUrl: value.logo_data_url ?? "",
    logoVisible: value.logo_visible,
    logoPosition: value.logo_position,
    logoSize: value.logo_size,
    handleVisible: value.handle_visible,
    handlePosition: value.handle_position,
    handleSize: value.handle_size,
    cover: fromPersistedSlide(value.cover),
    inside: fromPersistedSlide(value.inside),
  }
  return normalizeDesign(Object.fromEntries(
    Object.entries(mapped).filter(([, fieldValue]) => fieldValue !== undefined),
  ) as DesignInput)
}

function withPrebuiltDesigns(saved: CarouselDesign[]): CarouselDesign[] {
  const savedIds = new Set(saved.map((design) => design.id))
  return [
    ...saved.map(refreshUntouchedPreset),
    ...PREBUILT_DESIGNS.filter((design) => !savedIds.has(design.id)).map(cloneDesign),
  ]
}

function readDesigns(): CarouselDesign[] {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (!stored) return PREBUILT_DESIGNS.map(cloneDesign)
    const parsed = JSON.parse(stored)
    if (!Array.isArray(parsed) || parsed.length === 0) {
      return PREBUILT_DESIGNS.map(cloneDesign)
    }
    const saved = parsed.map((design) => normalizeDesign(design as DesignInput))
    return withPrebuiltDesigns(saved)
  } catch {
    return PREBUILT_DESIGNS.map(cloneDesign)
  }
}

export function useCarouselDesigns() {
  const [designs, setLocalDesigns] = React.useState<CarouselDesign[]>(readDesigns)
  const [syncStatus, setSyncStatus] = React.useState<DesignSyncStatus>("loading")
  const hydrated = React.useRef(false)
  const changedBeforeHydration = React.useRef(false)
  const latestDesigns = React.useRef(designs)
  const saveChain = React.useRef<Promise<unknown>>(Promise.resolve())
  const mounted = React.useRef(true)

  const setDesigns = React.useCallback<React.Dispatch<React.SetStateAction<CarouselDesign[]>>>((change) => {
    if (!hydrated.current) changedBeforeHydration.current = true
    setSyncStatus("saving")
    setLocalDesigns(current => (typeof change === "function" ? change(current) : change).map(design => ({ ...design, cover: coverLayout(design.cover) })))
  }, [])

  React.useEffect(() => {
    latestDesigns.current = designs
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(designs))
  }, [designs])

  React.useEffect(() => {
    mounted.current = true
    let cancelled = false

    async function loadLibrary() {
      try {
        const response = await get<DesignLibraryResponse>("/api/designs")
        if (cancelled) return
        hydrated.current = true
        if (response.items.length > 0 && !changedBeforeHydration.current) {
          const remote = withPrebuiltDesigns(response.items.map(fromPersistedDesign))
          latestDesigns.current = remote
          setLocalDesigns(remote)
          setSyncStatus("synced")
          return
        }
        const current = latestDesigns.current
        setSyncStatus("saving")
        saveChain.current = saveChain.current
          .catch(() => undefined)
          .then(() => put<DesignLibraryResponse>("/api/designs", {
            items: current.map(designPayload),
          }))
        await saveChain.current
        if (!cancelled && latestDesigns.current === current) setSyncStatus("synced")
      } catch {
        hydrated.current = true
        if (!cancelled) setSyncStatus("offline")
      }
    }

    void loadLibrary()
    return () => {
      cancelled = true
      mounted.current = false
    }
  }, [])

  React.useEffect(() => {
    if (!hydrated.current) return
    const timer = window.setTimeout(() => {
      const snapshot = designs.map(designPayload)
      setSyncStatus("saving")
      saveChain.current = saveChain.current
        .catch(() => undefined)
        .then(() => put<DesignLibraryResponse>("/api/designs", { items: snapshot }))
        .then(() => {
          if (mounted.current && latestDesigns.current === designs) setSyncStatus("synced")
        })
        .catch(() => {
          if (mounted.current && latestDesigns.current === designs) setSyncStatus("offline")
        })
    }, 600)
    return () => window.clearTimeout(timer)
  }, [designs])

  return [designs, setDesigns, syncStatus] as const
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
  design = { ...design, cover: coverLayout(design.cover) }
  return {
    id: design.id,
    name: design.name,
    handle_text: design.handleText ?? "",
    logo_data_url: design.logoDataUrl ?? "",
    logo_visible: design.logoVisible,
    logo_position: design.logoPosition,
    logo_size: design.logoSize,
    handle_visible: design.handleVisible,
    handle_position: design.handlePosition,
    handle_size: design.handleSize,
    cover: {
      logo_visible: design.cover.logoVisible,
      handle_visible: design.cover.handleVisible,
      shadow_visible: design.cover.shadowVisible,
      shadow_opacity: design.cover.shadowOpacity,
      shadow_height: design.cover.shadowHeight,
      shadow_softness: design.cover.shadowSoftness,
      shadow_color: design.cover.shadowColor,
      title_size: design.cover.titleSize,
      title_position: design.cover.titlePosition,
      title_align: design.cover.titleAlign,
      font_family: design.cover.fontFamily,
      background: design.cover.background,
      text_color: design.cover.textColor,
      highlight_text_color: design.cover.highlightTextColor,
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
      shadow_visible: design.inside.shadowVisible,
      shadow_opacity: design.inside.shadowOpacity,
      shadow_height: design.inside.shadowHeight,
      shadow_softness: design.inside.shadowSoftness,
      shadow_color: design.inside.shadowColor,
      title_size: design.inside.titleSize,
      title_position: design.inside.titlePosition,
      title_align: design.inside.titleAlign,
      font_family: design.inside.fontFamily,
      background: design.inside.background,
      text_color: design.inside.textColor,
      highlight_text_color: design.inside.highlightTextColor,
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
