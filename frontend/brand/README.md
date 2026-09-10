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
| Canvas | Warm ivory `#F6F4F0` | Charcoal `#191A1C` |
| Surface | Porcelain `#FFFEFB` | Graphite `#222427` |
| Navigation | Sand `#EFECE6` | Deep graphite `#1E1F21` |
| Text | Ink `#252420` | Warm white `#F4F1EB` |
| Secondary text | Stone `#706A62` | Silver `#ABABA9` |
| Primary action | Burnt orange `#C74726` | Apricot `#F79270` |
| Action text | White `#FFFFFF` | Dark brown `#271B16` |
| Selected surface | Pale peach `#FBE7DE` | Dark clay `#3D2B26` |
| Divider | `#DEDAD2` | `#3B3D40` |

The logo keeps its burnt-orange tile in either theme. Interactive colours adapt to the canvas. Status colours are separate: blue for generation, green for checks and completion, amber for review, and red for failures. Always pair semantic text with its matching soft-surface token.

## Typography and interface

System sans-serif for navigation, controls, and data. Georgia italic provides the editorial accent in the creation and sign-in headlines. No remote fonts are required. Use generous space around the composer, quiet borders, and restrained shadows. The agent introduction describes existing creative roles; it does not claim live availability.

The theme lives in `src/index.css`, with studio layouts in `src/studio.css`. Browser chrome colours in `index.html` and `src/hooks/use-theme.ts` must match the canvas tokens. Generated carousel artwork, publishing identities, and agent behaviour are independent of this application theme.
