# Design System: Pathfinder

> Source of truth for generating new Pathfinder screens in Google Stitch.
>
> **Every surface now runs one design system** — the warm-monochrome minimal
> system in Part A. Part B is retained only as a record of the retired
> neo-brutalist language; do not generate new screens from it.
>
> | Surface | Scope classes | Density |
> |---|---|---|
> | Home / marketing | `.tv2` | 4 — airy |
> | Sign in, sign up, Explore, Account | `.tv2.tv2-app` | 7 — working |
> | Planner (`/create`) | `.tv2.tv2-app.tv-create` (+ `.is-dark`) | 7 — working |
>
> Stylesheets: `pathfinder-v2.css` (tokens, type, primitives) ·
> `pathfinder-v2-components.css` (marketing) · `pathfinder-v2-app.css` (app
> shell, forms, auth, explore, profile, chat) · `pathfinder-v2-planner.css`
> (planner sidebar, spatial panel, route curtain).

## Brand mark

The logo is `frontend/media/icons/Vector.svg` — an arc horizon breaking into a
trailing stroke. It is rendered everywhere through one component,
`components/common/Logo.tsx` (`<Logo />` and `<LogoLockup />`). Never redraw the
mark inline or substitute another glyph.

## App-surface additions

- **Dark mode** exists on the planner only (`.is-dark`): canvas `#131313`,
  surface `#1B1B1B`, ink `#F4F3F1`, hairline `#2A2A2A`. Primary buttons invert.
- **Transcript** — mono header line (who · when) over a hairline block; the
  user's entry is inverted ink. Never chat bubbles with tails.
- **Composer** — one row on every viewport: attach · input · dictate · Plan.
- **Route curtain** — every navigation rises a bone panel carrying the mark
  and destination label, swaps the view while covered, and lifts. Instant
  swap under reduced motion.

---
---

# Part A — Design system (`.tv2`)

Implemented in `frontend/src/styles/pathfinder-v2.css`,
`pathfinder-v2-components.css`, and `frontend/src/components/home/v2/`.

## A1. Visual Theme & Atmosphere

A warm-monochrome, document-style interface — closer to a well-set printed
journal than to a travel app. Depth comes from **hairlines and whitespace**,
never from heavy shadow. Photography is the only chromatic element on the page;
every surface around it stays achromatic so the pictures carry all the colour.

The signature move is **inline image typography**: small photographs set
between the words of the hero headline at type height, acting as punctuation
rather than illustration.

**Calibration:** Density **4** (airy, macro-whitespace) · Variance **7**
(asymmetric hairline grids) · Motion **7** (choreographed, weighted, restrained).

## A2. Colour Palette & Roles

Achromatic. There is no brand hue — emphasis comes from contrast, not colour.

**Light (the only marketing theme)**
- **Bone Canvas** (`#F7F6F3`) — page background
- **Paper** (`#FFFFFF`) — card and panel fill
- **Sunk** (`#F9F9F8`) — input wells, inert regions
- **Ink** (`#111111`) — primary text, primary button fill
- **Ink Soft** (`#2F3437`) — body copy
- **Muted** (`#787774`) — secondary copy, italic headline clauses
- **Faint** (`#9B9A97`) — eyebrows, metadata
- **Hairline** (`#EAEAEA`) — every border and divider, always exactly 1px

**Inverted** (`.tv-invert`, used on the dark CTA band): ink becomes `#F4F3F1`,
hairlines become `rgba(255,255,255,0.16)`.

**Washed pastel spots** — semantic only, never decoration:
`#E1F3FE`/`#1F6C9F` blue · `#EDF3EC`/`#346538` green ·
`#FBF3DB`/`#956400` yellow · `#FDEBEC`/`#9F2F2D` red.

## A3. Typography

- **Display — `Instrument Serif`**: all headlines. Tracking `-0.028em`, line-height
  `0.98`–`1.22`. Italic (`<em>`) sets the second clause of a headline in `Muted`,
  which is how hierarchy is expressed inside a single sentence.
- **UI & body — `Geist`**, 300–600. Body leading `1.62`, leads capped at `54ch`.
- **Metadata — `Geist Mono`**: costs, durations, coordinates, city codes, eyebrows,
  labels, table figures. Always `font-variant-numeric: tabular-nums`.
- **Banned:** `Inter`, `Roboto`, `Open Sans`; generic serifs (`Georgia`,
  `Times New Roman`, `Garamond`); gradient or outlined text.

## A4. Components

- **Buttons** — 4px radius, 46px min height. Primary is `Ink` fill / `Bone` text;
  ghost is transparent with a hairline. No shadow. `scale(0.982)` on press.
- **Cards & panels** — `1px solid #EAEAEA`, 12px radius, generous internal padding
  (`clamp(1.4rem, 2.2vw, 2rem)`). Hover lifts to `0 2px 8px rgba(0,0,0,0.04)` — never more.
- **Tags** — pill radius (the one permitted use), mono, uppercase, `0.07em` tracking.
- **Keycaps** — `<kbd>`-style, mono, 1px border with a 2px bottom edge.
- **Faux-OS window** — used for agent mockups: hairline frame, bone title bar,
  three grey dots.
- **Accordion** — no container boxes; hairline dividers with a sharp `+` / `−`.
- **Icons** — hand-drawn set in `IconsV2.tsx`, uniform 1.75 stroke on a 24px grid.
  No Lucide, Feather, or Heroicons.

## A5. Layout

- **Hero: asymmetric bento.** Six-column hairline grid — headline cell (4×2),
  tall photo (2×2), live trace (2), costed rows (2), stat (2), wide photo band (6).
- The **bento language belongs to the hero alone.** Do not repeat it for a
  content section; the page would read as two versions of the same idea.
- Container `1240px` (`--tv-max`), wide variant `1500px`, gutter
  `clamp(1.25rem, 4vw, 3rem)`. Section rhythm `clamp(5rem, 11vw, 9rem)`.
- Full-bleed rails inset their first item to the container edge and set a
  matching `scroll-padding-left`, or scroll-snap drags the first card to the
  viewport edge and breaks alignment with the heading.
- Full-height uses `min-h-[100dvh]`; `h-screen` is banned.

## A6. Motion

House curve is `expo.out`, ~0.7–1.3s. Nothing bounces except the inline hero
photos, which use `back.out(1.7)` as a deliberate single flourish.

- **Smooth scroll** — Lenis driven by `gsap.ticker`, forwarding to
  `ScrollTrigger.update()`, with `lagSmoothing(0)`. Never run Lenis on its own rAF
  loop alongside ScrollTrigger; pins drift.
- **Headlines** — `SplitText` line masks, `yPercent: 115`, `0.09` stagger.
- **Panels** — `clip-path: inset(0 0 100% 0)` wipe plus a short rise.
- **Pinned horizontal** — the capabilities section pins and translates its track
  sideways. The horizontal tween **must** use `ease: 'none'`; nested reveals key
  off it via `containerAnimation`.
- **Parallax** — `.tv-img--drift` images are oversized (130% height, `-15%` top)
  so a ±12% drift never exposes an edge.
- **Marquee** — content rendered twice, `xPercent: -50`, linear, infinite.
- **Reduced motion** — `gsap.matchMedia` drops the pin entirely and every
  component early-returns. Content must be visible with zero animation.
- Animate `transform` / `opacity` / `clip-path` only.

## A7. Anti-Patterns (Banned)

- Bento grids outside the hero
- Shadows heavier than `0 2px 8px rgba(0,0,0,0.04)`; any `shadow-md`/`lg`/`xl`
- Gradients, neon, glassmorphism (a subtle navbar blur is the sole exception)
- `rounded-full` on cards or primary buttons
- Pure black `#000000` as a text or surface colour
- Emojis anywhere, including alt text
- Three equal cards in a row
- Placeholder names ("John Doe", "Acme", "Lorem ipsum")
- Fake round statistics ("99.99% uptime", "10,000+ travellers")
- AI copywriting clichés: "Elevate", "Seamless", "Unleash", "Next-Gen", "Delve"
- Filler UI text: "Scroll to explore", bouncing chevrons
- A `from` tween stacked on targets another helper already animated — the second
  records the mid-animation state and the content never becomes visible
- Setting `display` at desktop width on a visibility helper: it collapses
  `<td>`/`<th>` out of table layout

---
---

# Part B — Retired application language (`.sharp-ui`)

> **Retired.** No live route uses this system any more; every surface was
> migrated to Part A. The `.sharp-ui` reset still exists in `index.css` and is
> harmless, but nothing opts into it. Kept for history only.

Formerly governed `/create`, `/explore`, `/login`, `/signup` and `/profile`,
which opted in via `.sharp-ui` — a `border-radius: 0 !important` reset plus the
tactile hard-shadow button lift.

## B1. Atmosphere

High-contrast editorial neo-brutalism: crisp ink-on-paper, absolute right
angles, hard offset shadows that behave like physical objects, and a square
drafting grid behind every working surface.

## B2. Colour

**Light:** Paper `#FFFFFF` · Bone `#F9F9F9` · Ash `#F5F5F5` · Draft Grey `#D9D9D9`
· Charcoal Ink `#1F1E1E` · Muted `rgba(31,30,30,0.7)`

**Dark:** Field `#1F1E1E` · Pitch `#151515` · Composer `#1A1A1A` · Bone Ink
`#FFFFFF` · Iron Edge `#555555` · True Shadow `#000000` (shadow colour only)

**Signals:** Alert Red `#DC2626` · Confirm Green `#059669`

## B3. Typography

`Jockey One` for the grand "PATHFINDER" wordmark only. `Lato` everywhere else —
700–900 uppercase for headings and controls, 400–500 for body. Monospace for all
tabular figures. No serifs.

## B4. Geometry & Components

- `border-radius: 0` on everything. No pills, no rounded cards, no circular avatars.
- 2px borders; hard offset shadows `3px 3px 0`. Hover lifts to `4px 4px 0` at
  `translate(-1px,-1px)`; press compresses to `1px 1px 0`.
- No glassmorphism, no diffuse shadows, no gradients.
- Chat transcript uses editorial headers over bordered blocks — never bubbles.
- Drafting grid: SVG pattern lines, 48px desktop / 32px mobile.

## B5. Banned

Any radius above 0 · soft shadows · glassmorphism · gradients · accent hues
beyond the two signals · pure black as text or surface · emojis · serifs.
