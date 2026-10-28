---
name: Transcriber
description: Lecture and meeting recordings as Cornell study sheets on ruled paper.
colors:
  ballpoint-blue: "#1f4fa3"
  blue-wash: "#e8eefa"
  red-pen: "#b3332b"
  margin-red: "#e0675e"
  highlighter: "rgba(255, 219, 51, 0.55)"
  highlighter-solid: "#ffe680"
  desk: "#e9ebe5"
  desk-2: "#dfe2db"
  paper: "#fdfdfb"
  ink: "#1d2430"
  ink-2: "#444d5c"
  ink-3: "#565e6b"
  pencil: "#5f6774"
  rule: "#dbe5f3"
  rule-strong: "#c3d1e6"
typography:
  display:
    fontFamily: "Literata, Iowan Old Style, Georgia, serif"
    fontSize: "clamp(1.4rem, 1.1rem + 1vw, 1.9rem)"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  headline:
    fontFamily: "Literata, Iowan Old Style, Georgia, serif"
    fontSize: "1.3rem"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "Literata, Iowan Old Style, Georgia, serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "Literata, Iowan Old Style, Georgia, serif"
    fontSize: "1.02rem"
    fontWeight: 400
    lineHeight: "1.75rem"
  body-summary:
    fontFamily: "Literata, Iowan Old Style, Georgia, serif"
    fontSize: "1.1rem"
    fontWeight: 500
    lineHeight: "1.75rem"
  ui:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  cue:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 500
    lineHeight: "1.75rem"
    fontFeature: "tnum"
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 600
    lineHeight: "1.75rem"
    letterSpacing: "0.08em"
rounded:
  hairline: "2px"
  xs: "3px"
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  line: "1.75rem"
  cue: "8.5rem"
  gutter: "1.25rem"
  sheet: "1.5rem"
  desk: "2rem"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0.35rem 0.75rem"
  button-primary:
    backgroundColor: "{colors.ballpoint-blue}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    padding: "0.35rem 0.75rem"
  button-quiet:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "0.35rem 0.75rem"
  button-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.red-pen}"
    rounded: "{rounded.sm}"
    padding: "0.35rem 0.75rem"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0.4rem 0.55rem"
  index-tab:
    backgroundColor: "{colors.desk-2}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.md}"
    padding: "0.45rem 0.95rem 0.5rem"
  index-tab-active:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  binder-divider:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.xs}"
    padding: "0.4rem 0.5rem"
  binder-divider-active:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  sheet:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.md}"
    padding: "1.1rem 1.5rem 1.5rem 0"
  timestamp:
    textColor: "{colors.ballpoint-blue}"
    typography: "{typography.cue}"
    rounded: "{rounded.hairline}"
    padding: "0 0.15rem"
  status-strip:
    backgroundColor: "{colors.blue-wash}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "0.55rem 0.8rem"
  dialog:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "1.4rem"
---

# Design System: Transcriber

## Overview

**Creative North Star: "The Cornell Sheet"**

Every recording is a Cornell study sheet lying on a desk. The sheet is ruled paper: a narrow cue column on the left holds timestamps and verdict marks, a single red margin rule divides it from the notes, and the summary is boxed across the top of the sheet. Around the sheet sits the student's desk: a binder rail of subjects and topics, and a stack of other sheets. The app reads like paper you study from, not like a dashboard of cards.

The pens carry the meaning. Ballpoint blue writes every time, link and confirmed point; red pen marks doubts and the checker's objections; a yellow highlighter marks points the notes may have missed and sweeps across the line you just jumped to. Reading text is set in Literata on the rules; controls and metadata are in the system UI face, with tabular figures throughout so times and counts line up. Night is the same sheet in deep navy with dim rules and chalk-white ink, following the system setting.

Density is calm and even: one 1.75rem line pitch governs the reading surface, and everything on the sheet sits on a ruled line. The world refuses floating cards, pills and chat-app sheen around a document.

**Key Characteristics:**
- Ruled paper with a 1.75rem line pitch; every reading row carries its own rules.
- A cue column (8.5rem, narrowing to 4.25rem on phones) right-aligned against one red margin rule.
- Three pens: ballpoint blue, red pen, highlighter yellow. No other accent; subject swatches only identify.
- Literata for what was said, the system UI face for controls, tabular figures everywhere.
- Index tabs and binder dividers in place of pills and segmented controls.
- One lifted-paper shadow; everything else is flat and separated by rules.
- Drawn single-stroke SVG line icons (1.75 stroke, round caps).

## Colors

A paper-and-pen palette: cool off-white paper on a grey-green desk, dark blue-black ink, and three pen colors with fixed meanings. Every color has a dark-mode counterpart defined on the same custom property under `prefers-color-scheme: dark` (values in the sidecar).

### Primary
- **Ballpoint Blue** (ballpoint-blue): every timestamp and link, section labels in the cue column, the "supported" verdict check, question text in Ask, the primary button, focus rings and the caret. In dark mode it becomes a soft periwinkle (#93b6ff).
- **Blue Wash** (blue-wash): the faint blue tint behind status strips, inherited-instruction notes, the drop zone on hover, menu-item hover and timestamp hover.

### Secondary
- **Red Pen** (red-pen): doubt and danger only. The "not found" verdict, checker reasons, errors, destructive buttons, failed queue states, removed lines in a proposal.
- **Margin Red** (margin-red): the single vertical margin rule between cue column and notes. Never used for text or fills.

### Tertiary
- **Highlighter** (highlighter): translucent yellow for the "Missed?" points, the sweep on a clicked time, added lines in a proposal, and text selection. Painted inside the text's x-height band (30% to 88%) like a real marker stroke.
- **Highlighter Solid** (highlighter-solid): opaque yellow for the missing-key banner and warning hints, where the yellow carries a whole block.

### Neutral
- **Desk** (desk) and **Desk Shade** (desk-2): the app background, top bar, stack of sheets, and (darker) the binder rail and inactive index tabs.
- **Paper** (paper): the open sheet, the active tab and divider, inputs, buttons, dialogs and menus.
- **Ink** (ink): primary text. **Ink 2** (ink-2): secondary text and quiet controls. **Ink 3** (ink-3): metadata, hints, placeholders, the cue column's resting text. Ink 3 was darkened to keep small text legible on both desk and paper.
- **Pencil** (pencil): provisional marks: the "partly" verdict, edited and unchecked marks, the "edited" stamp, suggested topics.
- **Rule** (rule) and **Strong Rule** (rule-strong): ruled lines on the sheet, and the stronger blue-grey for borders of the sheet, tabs, inputs, buttons and dividers between panes.

### Named Rules
**The Three Pens Rule.** Blue confirms and links, red doubts, yellow flags what may be missing. A color is never used for a meaning that belongs to another pen, and no fourth accent exists. The eight subject swatches on binder dividers identify a subject and nothing else: never text, fills or states.

**The One Margin Rule.** A sheet has exactly one red margin rule, between the cue column and the notes, and it runs continuously through the summary box.

## Typography

**Display Font:** Literata (with Iowan Old Style, Georgia, serif)
**Body Font:** Literata for reading text; system-ui (with -apple-system, Segoe UI, Roboto) for interface text
**Label/Mono Font:** system-ui with tabular figures; no monospace face

**Character:** A bookish, optical-size serif for everything that was said or written, against a neutral system face for everything you operate. The split tells the student at a glance what is content and what is chrome.

### Hierarchy
- **Display** (600, clamp(1.4rem, 1.1rem + 1vw, 1.9rem), 1.2, -0.015em): the recording title, an editable field sitting on the sheet's top line.
- **Headline** (600, 1.3rem, 1.2): dialog titles.
- **Title** (600, 1rem, 1.3): the stack heading; sheet-list titles use 500 at 0.95rem; the wordmark uses 600 at 1.15rem.
- **Body** (400, 1.02rem, line-height 1.75rem): note points, transcript paragraphs, answers, my-notes; the summary box raises it to 500 at 1.1rem.
- **UI** (400, 15px, 1.5, tabular figures): controls, metadata, buttons at 0.875rem.
- **Cue** (500, 0.8rem, 1.75rem, tabular figures): timestamps in the cue column.
- **Label** (600, 0.72rem, 0.08em tracking, uppercase): cue-column section labels (in blue), binder headings, part breaks and field labels (in ink 3).

### Named Rules
**The Two Voices Rule.** Literata is for what was said and what the student writes; the system face is for controls, labels and metadata. Checker reasons are Literata italic in red pen, prefixed by a small uppercase UI label.

**The Tabular Figures Rule.** Every time, date and count uses tabular figures (set on body), so cue columns and lists align.

## Layout

Desktop is three panes on a full-height grid: binder rail (13rem), stack of sheets (19rem), and the desk (remaining width), where the open sheet is centered at a 56rem max width with 1.75rem 2rem desk padding. Below 1180px the panes narrow to 11.5rem and 17rem and the cue column to 7rem. Below 860px the app becomes a single column: the sheet alone under a top bar, with binder and stack in a slide-in drawer (min(22rem, 88vw)) over a dimmed desk; the cue column narrows to 4.25rem, cue-column section labels move to the notes side of the margin rule, and timestamps stack under verdict marks.

The sheet itself is a two-column Cornell grid: cue column (spacing.cue) with a red right border, then notes indented by the gutter (1.25rem; 0.75rem on phones). Vertical rhythm on the sheet is the line pitch (spacing.line): rows, gaps between sections, and textarea heights are multiples of it.

**The On-the-Line Rule.** Every reading row on the sheet (note rows, transcript paragraphs, Q&A) paints its own repeating rule at the line pitch and sets its text line-height to that pitch, so ink always sits on a line. New sheet content joins the grid rather than floating over it.

## Elevation & Depth

Depth is paper on a desk. The system is flat: panes are separated by strong rules and by the desk/desk-shade/paper tonal steps. One shadow exists, the lift, and it belongs only to paper lying above the desk.

### Shadow Vocabulary
- **Lift** (light: `0 1px 2px rgba(29, 36, 48, 0.08), 0 10px 28px -12px rgba(29, 36, 48, 0.22)`; dark: `0 1px 2px rgba(0, 0, 0, 0.4), 0 12px 30px -14px rgba(0, 0, 0, 0.7)`): the open sheet, dialogs, the overflow menu panel, the toast and the phone drawer.

### Named Rules
**The Lifted Sheet Rule.** Only paper that sits above the desk takes the lift. List rows, tabs, buttons, boxes and strips stay flat; they are separated by rules and tone, never by shadow.

## Shapes

Small, paper-cut corners. Radii step from 2px (stamps, timestamp hover) through 3px (binder dividers, summary box) and 4px (buttons, inputs, list rows, strips) to 6px (the sheet, index tabs, menus, the drop zone, toast) and 8px (dialogs). The sheet's top-left corner is square (0 6px 6px 6px) because the active index tab joins it there; tabs are rounded on top only and overlap the sheet's top border by 1px. Lines do most of the shaping: 1px rules, a dashed 1.5px border on the drop zone, a dotted 2px rule for part breaks in a combined recording, and underline-only fields for inline editing (topic add, add-a-point, subject select).

## Components

### Buttons
Plain stationery: paper face, thin blue-grey border, nothing raised.
- **Shape:** gently squared (rounded.sm, 4px), 0.35rem 0.75rem, 0.875rem UI text, optional leading line icon.
- **Default:** paper background, strong-rule border, ink text. Hover darkens the border to ink 3.
- **Primary:** ballpoint-blue fill and border with paper text; hover brightens slightly. One per dialog or form (Save, Transcribe).
- **Quiet:** no fill, transparent border, ink 2 text; hover shows the strong-rule border. Used for Cancel, Clear and tab-line actions.
- **Danger:** default button with red-pen text (Delete subject, Delete topic). Destructive actions also confirm.
- **Icon button:** borderless, ink 2, 0.3rem padding; hover shows ink text and a strong-rule border.
- **Focus:** 2px ballpoint-blue outline, 2px offset, on every control.

### Inputs / Fields
- **Style:** paper background, 1px strong-rule border, 4px radius, 0.4rem 0.55rem padding; placeholders in ink 3.
- **Inline fields on the sheet:** underline only (1px strong rule, no box, no radius): subject select, topic add, add-a-point. The title is borderless until hover or focus.
- **My notes:** a borderless textarea that paints the ruled lines itself and scrolls with them.

### Navigation
- **Index tabs (Notes, Ask, Transcript, My notes):** desk-shade folder tabs, strong-rule border open at the bottom, 6px top corners, sitting on the sheet's top edge. The active tab turns paper and 600 weight and merges with the sheet. Primary actions (Copy, the overflow menu) sit on the tab line to the right.
- **Binder dividers:** full-width rows in the binder rail with a colored swatch (0.55rem by 0.95rem, tab-shaped corners) per subject from a fixed eight-color set, name and count. Hover tints to desk; active turns paper and 600. Topics use the same row with a `#` prefix in ink 3. Section headings are uppercase labels.
- **Stack rows:** borderless sheet entries with a date block (Literata day number over an uppercase month) and title, meta and state lines; hover and selected states turn paper with a rule or strong-rule border. Queue state is written in blue, failures in red pen.
- **Phone:** top bar with a menu button opens the drawer; tabs tighten to 0.55rem side padding and action labels hide behind icons.

### Cards / Containers
- **The sheet:** paper, strong-rule border, lift shadow, 0 6px 6px 6px radius, padding 1.1rem 1.5rem 1.5rem with no left padding so the cue column reaches the edge.
- **Summary box:** a 1px strong-rule box with 3px corners, inset 0.6rem on the sheet, holding the summary rows in 500 weight; the margin rule stays continuous through it.
- **Status strip:** blue-wash strip (4px radius) for transcription and note progress with a pulsing blue dot; errors drop the fill and turn red pen. Status strips announce politely as status regions.
- **Proposal (Ask):** paper box with strong-rule border; added lines highlighted, removed lines struck through in red pen; apply or dismiss explicitly.
- **Dialogs:** paper, 8px radius, lift shadow, 1.4rem padding, Literata title, labels stacked over fields, actions right-aligned with destructive actions pushed left.

### Signature: the Cornell note row
Cue column holds the verdict mark (check in blue for supported, half-filled circle in pencil for partly, crossed circle in red for not found, dot or pencil for unchecked and edited) and the timestamp; the notes side holds the point in Literata, owner and due in small UI text, and a checker reason in red italic below. Edited points carry a small pencil "edited" stamp, uppercase and tilted -2deg. On desktop, edit and delete tools appear on hover at the right edge; on phones they appear when the row is tapped, as 44px bordered targets, and delete asks for confirmation.

### Signature: the highlighter sweep
Clicking a timestamp plays the audio from that moment and sweeps a highlighter stroke across that note line: the text's inline ink spans animate their background from 0% to 100% width over 0.7s on cubic-bezier(0.16, 1, 0.3, 1), wrapping line by line. Missed points carry the stroke at rest. Reduced motion shows the stroke without the sweep.

## Do's and Don'ts

### Do:
- **Do** put every time in the cue column as a ballpoint-blue, tabular-figure timestamp that plays the audio from that moment.
- **Do** size sheet content in multiples of the 1.75rem line and let each row draw its own rules.
- **Do** keep the three pens to their meanings: blue confirms and links, red doubts and destroys, yellow flags missing points and the jump-to line.
- **Do** set content in Literata and controls in the system UI face with tabular figures.
- **Do** draw new icons as 24px single-stroke SVG symbols in the sprite (stroke 1.75, round caps and joins, no fill).
- **Do** define every new color as a custom property with a dark-mode value.
- **Do** honor reduced motion: the sweep, the pulse and the drawer slide all stop.

### Don't:
- **Don't** wrap content in floating cards or pills; use rules, tabs, dividers and the boxed summary.
- **Don't** give the lift shadow to anything that is not paper lying above the desk.
- **Don't** add a second accent color or a second margin rule.
- **Don't** use font glyphs, emoji or icon fonts for icons.
