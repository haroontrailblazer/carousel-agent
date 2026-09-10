# Carousel Factory — Studio identity

The mark combines two offset slide frames into a C, with a forward swipe at its centre. It represents carousel creation and the handoff between agents. The application name remains Carousel Factory.

## Assets

- `../public/logo.svg`: primary vector mark, used in the app and as its favicon. Scales without raster blur; 64 × 64 viewBox.
- `mark-monochrome.svg`: single-colour version for print or restrained applications. Set `color` when embedding inline; black by default.
- The former WebP artwork is retained as an archive, but is no longer used by the UI.

Use the mark at 24px or larger in the interface. The favicon may use 16px. Leave at least a quarter of the mark's width clear on each side. Keep the original aspect ratio and colours.

## Palette

| Role | Light | Dark |
| --- | --- | --- |
| Canvas | Warm ivory `#F6F4F0` | Pitch black `#000000` |
| Surface | Porcelain `#FFFEFB` | Raised black `#0C0C0C` |
| Navigation | Sand `#EFECE6` | Pitch black `#000000` |
| Text | Ink `#252420` | Warm white `#F4F1EB` |
| Secondary text | Stone `#706A62` | Silver `#A6A6A6` |
| Primary action | Burnt orange `#C74726` | Apricot `#F79270` |
| Action text | White `#FFFFFF` | Dark brown `#271B16` |
| Selected surface | Pale peach `#FBE7DE` | Dark clay `#3D2B26` |
| Divider | `#DEDAD2` | `#292929` |

The logo keeps its burnt-orange tile in either theme. Interactive colours adapt to the canvas. Status colours are separate: blue for generation, green for checks and completion, amber for review, and red for failures. Always pair semantic text with its matching soft-surface token.

## Typography and interface

The studio uses a coordinated set of realistic 3D-style illustrations in porcelain, glass, and orange metal. See [assets and generation prompts](3d/README.md) for originals, responsive exports, and usage details.

System sans-serif for navigation, controls, and data. Georgia italic provides the editorial accent in the creation and sign-in headlines. No remote fonts are required. Use generous space around the composer, quiet borders, and restrained shadows.

The theme lives in `src/index.css`, with studio layouts in `src/studio.css`. Browser chrome colours in `index.html` and `src/hooks/use-theme.ts` must match the canvas tokens. Generated carousel artwork, publishing identities, and agent behaviour are independent of this application theme.

## Depth and motion

`src/studio-depth.css` supplies the shared surface highlights, soft shadows, and press feedback. `StudioEmblem` reuses the existing optimized illustrations in page headings, chat activity, trace agents, and the review inspector. Navigation selection uses a soft exterior shadow without a left accent line.

Artwork enters once, then responds gently to pointer hover. Reduced-motion preferences disable both effects. Reading surfaces and carousel previews stay level; the design editor's canvas coordinates are unaffected. Primary controls, focus rings, task filters, and Instagram review prerequisites retain their existing behavior.

Starting a carousel uses a shared-element transition from the composer to the submitted prompt, followed by the chat workspace. `LaunchHandoff` keeps the submitted text and selected design visible while creation and the first snapshot are pending. It adds no artificial network delay; errors restore the original composer and keyboard focus. Reduced-motion and browsers without View Transitions retain the same status feedback without the moving transition.


## Visual design editor

The Designs page uses a large 4:5 sample carousel with a single contextual control panel. Studio Light and Studio Black are new starter designs that share the app's ivory, ink, orange, and pitch-black palette. Existing saved designs retain their settings. Palettes update both slide types; individual color controls affect the current slide.

Text, the inside-slide image, logo, and handle can be dragged and resized. Arrow keys nudge an object; precise position controls are collapsed by default. One Undo restores a whole drag. Preview removes editing handles and makes the canvas inert. Cover media remains a full-canvas background, matching the existing render contract.

Preview wording, artwork, and branding are samples, not a generated export. Inside headline and body move together because the renderer uses one text region. Typography scales with the 1080px slide width; final font fitting and generated content remain the renderer's responsibility. The editor reuses the existing optimized artwork and adds no remote asset dependencies.

Changes save automatically through the existing design library API. The save indicator includes the debounce period, and older saves cannot mark newer edits as saved. Use design becomes available after sync and opens New carousel with that design selected.
