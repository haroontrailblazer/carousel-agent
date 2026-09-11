# Design Skill - Carousel System

This is the production design guide for body and CTA slides. The selected
CarouselDesign saved on the run is the authority for identity, colors, fonts,
visibility and element positions. This application serves independent creators
and brands. Never assume a particular creator, website, favicon or handle.

## Selected design contract

- Use the saved inside layout for body slides and the saved CTA layout for the
  final slide. The cover has its own layout. Do not substitute a default layout
  or reuse the body layout when a separate CTA layout is present.
- Use each surface's exact background, text, highlight and accent colors.
  Orange, green and other user-selected colors are equally valid. Default
  palette values apply only to designs that omit their own values.
- Use the design's uploaded logo and exact handle. When absent, the run's
  account identity may supply them. Never infer identity from reference art,
  another customer's carousel, prior QA messages or historical feedback.
- Respect both global and per-surface logo/handle visibility. A hidden logo,
  a handle-only design, a logo-only design or an unbranded run is valid.
- Each saved x/y/width/height transform is a percentage of the full canvas.
  Keep the handle left-aligned and vertically centered in its box. Contain
  the original logo within its box without distortion. Do not move either
  element to a fixed footer anchor when a saved transform exists.
- Historical feedback may improve clarity, but cannot override this run's
  selected identity or layout. Never demand a different brand's favicon.

## Format and type

- Canvas: 1080 x 1350 px, portrait 4:5.
- Use the selected font, title size, alignment, safe margin and title box.
  The deterministic compositor fits approved text to readable size limits.
- Keep body text readable at feed size. If approved copy cannot fit at the
  minimum readable size, request shorter copy upstream without changing facts.
- Approved copy is rendered verbatim, with no paraphrasing or added claims.
- Body slides have one deterministic two-digit sequence at x=88, y=76.
  The first body slide is 01. Cover and CTA slides are unnumbered.

## Carousel rhythm

Give every slide one job and one dominant visual. A sequence can move from
statement to explanation, evidence, mechanism, implication, takeaway and CTA.
Vary composition while keeping the selected visual identity consistent.
Avoid repetitive headline-and-bullet slides and grids of decorative cards.

## Body slide template

Choose the visual that best explains the approved copy:

- Editorial explainer: one idea, a compact paragraph and an explanatory object.
- Data evidence: an honest number, comparison or chart supported by research.
- Process: one continuous sequence through two to four steps.
- Comparison: one balanced, meaningful contrast.
- Technical proof: a restrained, source-grounded interface or mechanism.
- Statement pause: one clear sentence and generous negative space.

There is no mandatory reference image or brand-specific template. Reference
art supplies composition only; ignore its sample text, palette and branding.

## Illustration, charts and proof

- The image model generates a standalone exact 2:1 visual panel. All text,
  slide numbers, logos and handles are composited deterministically afterward.
  Never generate letters, digits, pseudo-writing, labels or watermarks.
- Keep every important subject visible without stretching. Unexpected aspect
  ratios are contained with padding, not distorted or cropped to lose content.
- The default lower visual zone is y=620..1160. The selected image transform
  determines final placement and scale. Do not bake header or footer bands into
  the generated visual panel.
- Follow the selected surface's art direction and background. Keep decorative
  colors restrained so the configured title highlight remains clear.
- Use source images as factual proof. Never invent charts, metrics or labels.
- Use one explanatory technique per slide and keep it away from the copy.

## CTA slide

Use the separately configured CTA design: its background, font, colors,
image choice, logo visibility, handle visibility and transform boxes.
Choose one action appropriate to the story:

- follow: a concrete value promise and the configured handle;
- comment: one specific question and a direct invitation to respond;
- redirect: what the deeper breakdown contains and the configured destination.

Keep the headline short and supporting copy compact. The footer uses only the
selected identity, if visible. Do not add extra identities, a slide number,
a swipe arrow, multiple actions, invented links or a mandatory external logo.

## Footer and QA contract

The deterministic compositor clears y=1160..1350 and draws a thin divider at
y=1160. Keep generated artwork free of baked-in footer furniture. Branding
uses saved transform boxes, including valid placements outside that default
rail. Legacy designs without boxes use their selected anchors and margins.

QA checks dimensions, body-number presence, divider and the current design's
visible brand marks at their configured positions. It must not require one
brand's logo colors, a fixed favicon location or marks intentionally hidden.
Only current tool results establish a QA failure. An obsolete QA message is
not evidence of a new defect and must not be described as a human rejection.

## Hard quality gates

- Render approved copy verbatim, using readable Latin-script transliterations.
- Never render an em dash, pseudo-text, invented quotes, sources or claims.
- Preserve contrast and readable text. Do not clip approved editorial copy.
- Avoid unrelated decorative objects, fake metrics and generic AI imagery.
- Keep the complete carousel visually consistent with the selected design.

## Learned rules (appended by the Learner agent - do not delete)
