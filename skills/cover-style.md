# Cover Style - First Page of Every Carousel

The cover is the strongest frame of the winning-carousel system. It uses the
existing sourced-media pipeline and overlay mechanics while adopting the
current Baskaran Builds site palette.

## Format

- 1080 x 1350 px (4:5). This aspect ratio governs every following slide.
- The cover remains a 4-15 second sourced video under the current runtime
  contract; a sourced still with a restrained push-in is the fallback.
- The media is never AI-generated. Use the announcement/event clip, product UI,
  paper figure, launch image, or another source-grounded visual.

## Brand tokens

- Ink: `#161811`.
- Primary text: `#E8E4D6`.
- Accent gradient: `#C8ED79` to `#B8EF43`.
- Muted: `#B9C5AA`.

Legacy orange is not used for new cover text or accent furniture.

## Composition

1. **Media zone (top ~62%).** Keep the subject/product recognizable and away
   from the title. Crop for one clear focal point; slight darkening is allowed.
2. **Grain dissolve (bottom ~38%).** Ink rises from the bottom through a
   stippled/noise edge, never a generic smooth gradient.
3. **Title block (lower third).** Maximum two lines when the words permit,
   condensed bold, warm-white, with exactly one verbatim phrase in lime.
4. **Continuity furniture.** Preserve the faint perspective floor/grid and
   compact side-arrow cues from the current overlay, recolored to lime.
5. **Brand rail.** Keep the lower edge quiet. Do not add a second headline,
   badges, stats, or source labels.

## Title rules

- Maximum 9 words total; 5-7 words is preferred.
- Lead with tension, consequence, or a surprising mechanism-not a generic
  announcement such as "X IS HERE".
- `hook_highlight` must be a verbatim substring of `hook_title` and contain the
  consequence or turn in the idea.
- Use punctuation only when it improves spoken rhythm; no emoji or hashtags.
- Never use an em dash. Use a period, comma, or colon instead.
- The title must remain readable at feed-thumbnail size.

Example shape: `YOUR AGENT LOOKS SMART UNTIL REALITY HITS`, highlighting
`REALITY HITS`.

## Image treatment

- Prefer real source imagery with useful negative space over generic cinematic
  AI imagery.
- Do not tint the whole media frame lime. Lime belongs only to the highlight
  phrase and small directional furniture.
- Avoid glows, lens flares, floating logos, and decorative circuit patterns.
- If the source is a paper or UI screenshot, keep one identifiable proof region
  visible rather than blurring the entire image behind the title.

## Legacy template note

`STRANGE-COVER (1).png` remains the geometry source for the grain dissolve,
grid, and arrows. Runtime compositing scrubs its example title and recolors its
legacy orange accents to the current lime token before rendering new copy.
