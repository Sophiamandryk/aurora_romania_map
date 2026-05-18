#!/usr/bin/env bash
# Start the Aurora Dashboard (API + open browser)
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "🗺️  Starting Aurora Dashboard…"

# Start API in background
uvicorn dashboard.api:app --port 8000 --host 127.0.0.1 &
API_PID=$!

echo "✅ API running at http://localhost:8000 (PID $API_PID)"
echo ""

# If running dev mode (no dist), start Vite dev server
if [ ! -d "$ROOT/dashboard/web/dist" ] || [ "$1" = "--dev" ]; then
  echo "📦 Starting Vite dev server at http://localhost:3000 …"
  cd "$ROOT/dashboard/web"
  npm run dev &
  VITE_PID=$!
  echo "🌐 Open: http://localhost:3000"
  trap "kill $API_PID $VITE_PID 2>/dev/null" EXIT INT TERM
  wait
else
  echo "🌐 Open: http://localhost:8000"
  trap "kill $API_PID 2>/dev/null" EXIT INT TERM
  # Open browser
  if command -v open &>/dev/null; then open "http://localhost:8000"; fi
  if command -v xdg-open &>/dev/null; then xdg-open "http://localhost:8000"; fi
  wait $API_PID
fi
