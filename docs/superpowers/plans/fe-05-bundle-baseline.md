# FE-05 Bundle Baseline

Measured via `npm run build:analyze` (vite 5.4.21, mode `analyze`, rollup-plugin-visualizer → `dist/report.html`).

Build notes:
- Warning: `Circular chunk: vendor-ui -> vendor-react -> vendor-ui` (manual chunk logic).
- `OrdersPage.tsx` is both dynamically and statically imported, so it stays in the main `index` chunk.
- No dedicated `vendor-grid` chunk exists in the current manualChunks config; the grid/table code lands in `index`.

## Baseline (before cleanup) — 2026-05-27

| chunk | raw (kB) | gzip (kB) |
|---|---|---|
| index | 494.02 | 138.86 |
| PieChart | 399.35 | 108.30 |
| vendor-react | 229.67 | 75.75 |
| vendor-ui | 132.78 | 40.13 |
| DashboardPage | 46.64 | 8.64 |
| OrderDetailPage | 43.97 | 12.02 |
| CustomersPage | 40.32 | 10.42 |
| index.css | 70.54 | 12.68 |

Biggest page chunks: DashboardPage, OrderDetailPage, CustomersPage, ChannelsPage (27.71/6.80), UsersPage (27.42/6.20).

## After cleanup — 2026-05-28

Same `npm run build:analyze` command after: dropping `@base-ui`, centralizing polling, splitting i18n.

| chunk | gzip before (kB) | gzip after (kB) | Δ gzip (kB) |
|---|---|---|---|
| vendor-react | 75.75 | 52.75 | -23.00 |
| vendor-ui | 40.13 | 40.08 | -0.05 |
| PieChart | 108.30 | 108.30 | 0.00 |
| index | 138.86 | 139.40 | +0.54 |
| index.css | 12.68 | 12.68 | 0.00 |

| chunk | raw before (kB) | raw after (kB) | Δ raw (kB) |
|---|---|---|---|
| vendor-react | 229.67 | 160.68 | -68.99 |
| vendor-ui | 132.78 | 132.68 | -0.10 |
| index | 494.02 | 495.14 | +1.12 |

### Notes / interpretation
- Biggest win: **vendor-react dropped ~69 kB raw / ~23 kB gzip**. Removing `@base-ui/react` (only used by `dialog.tsx`) eliminated the `vendor-ui -> vendor-react -> vendor-ui` circular-chunk dependency, so React-internal code is no longer duplicated/pulled into the react vendor chunk. The in-house dialog implementation reuses existing primitives (createPortal + context, same pattern as `popover.tsx`).
- `vendor-ui` barely moved at gzip level because the dialog code that left it was small relative to the rest of the UI vendor bundle.
- `index` grew marginally (+1.1 kB raw / +0.5 kB gzip). The i18n split recomposes via spread into the same `MESSAGES` object (verified byte-identical: vi=614 keys, en=610 keys before and after), which adds a few spread/import statements; net neutral for users, big maintainability win (1326-line file → 20 namespace files).
- No chunk exceeds the `chunkSizeWarningLimit` of 1200 kB. `PieChart` (399 kB raw) remains the largest single lazy chunk — out of scope for this cleanup.
