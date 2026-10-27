# Frontend

React + TypeScript dashboard starter built with Vite. The current screen uses clearly labeled illustrative preview data and has navigation for overview, invoices, inventory, and forecasts. Buttons do not upload documents or connect to the API yet. Keep AWS calls behind the backend API; the browser should only receive short-lived presigned upload URLs.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Build a static production bundle with `npm run build`; Vite writes it to `dist/` for later hosting in S3 behind CloudFront. Vite's current guide documents the React TypeScript template and notes its supported Node.js versions; check the local Node version if installation reports an engine mismatch: [Vite getting started](https://vite.dev/guide/).
