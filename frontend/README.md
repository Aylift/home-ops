# Frontend (Vue 3 + Vite)

Dashboard for the home-ops climate system. Served by the FastAPI backend in
production (built into the backend image); run standalone here for UI work.

## Local development

No backend required — a mock API is bundled as a Vite plugin
([`mock-api.js`](mock-api.js)) and serves every endpoint the dashboard calls
with plausible data (telemetry, history, events, weather, node status, and the
signed fan override).

```sh
npm install
npm run dev:mock
```

Open the printed URL (default `http://localhost:5173`). The dev server binds to
`0.0.0.0` (`server.host: true`), so you can also open it from a phone on the
same LAN at `http://<your-pc-ip>:5173` to test the real mobile layout.

### Testing mobile layout

In Chrome DevTools: `Ctrl+Shift+M` (device toolbar) → pick "iPhone 16" or any
device, or drag the viewport down to ~360 px. The layout is built mobile-first
with `sm:`/`md:` breakpoints.

### Against the real backend

```sh
# Point at a running backend (e.g. the server on the LAN):
VITE_API_URL=http://<server-ip> npm run dev
```

`VITE_API_URL` is read in [`src/App.vue`](src/App.vue) (`const API = ...`).
Leave it unset when the app is served by the backend (same origin).

## Build

```sh
npm run build   # -> dist/, copied into the backend image by backend/Dockerfile
```

## Mock API notes

- Enabled only when `MOCK_API=1` (set by `npm run dev:mock` via
  [`scripts/dev-mock.mjs`](scripts/dev-mock.mjs)). Never included in a build.
- Override state is in-memory and mirrors the real semantics: the sign is the
  desired state, the magnitude is the duration, same sign extends, opposite sign
  replaces.
- Telemetry is generated from slow sine waves so the history chart has shape.
