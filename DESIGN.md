# Design

## Theme
Database Agent is a local product UI for focused schema exploration. The visual mood is "survey instrument on a white lab bench": precise, restrained, and readable for long sessions.

## Color
Use OKLCH tokens only.

```css
:root {
  --bg: oklch(1 0 0);
  --surface: oklch(0.972 0.004 40);
  --surface-strong: oklch(0.935 0.008 40);
  --ink: oklch(0.18 0.018 40);
  --muted: oklch(0.46 0.018 40);
  --line: oklch(0.88 0.008 40);
  --primary: oklch(0.5 0.151 40);
  --primary-strong: oklch(0.43 0.15 39);
  --accent: oklch(0.42 0.12 205);
  --success: oklch(0.48 0.12 152);
  --warning: oklch(0.66 0.13 78);
  --danger: oklch(0.55 0.16 28);
}
```

## Typography
Use `Inter`, `Segoe UI`, and `system-ui` as the product font stack. Keep type fixed in `rem`, not fluid. Use compact labels, tabular numbers where useful, and avoid display typography inside the app shell.

## Layout
Use a three-zone product shell: top command/status bar, left navigation/filter rail, central graph canvas, and right inspector. Collapse side panels below tablet widths and prioritize search, graph, and selected-node details.

## Components
Controls use 8px radius, clear hover/focus states, and consistent borders. Cards are reserved for repeated graph search results or inspector groups; the app shell itself should be pane-based.

## Motion
Use short 150-220ms transitions for panel changes, hover feedback, and graph selection. Respect `prefers-reduced-motion` by disabling nonessential transitions.
