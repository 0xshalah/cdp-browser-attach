#!/usr/bin/env bash
# CDP Browser Attach — Auto Setup Script
# Detects OS, kills Chrome, launches with CDP, configures Hermes
set -e

CDP_PORT=9222
HERMES_CONFIG="$LOCALAPPDATA/hermes/config.yaml"

detect_os() {
  case "$(uname -s)" in
    Linux*)     echo "linux";;
    Darwin*)    echo "macos";;
    CYGWIN*|MINGW*|MSYS*) echo "windows";;
    *)          echo "unknown";;
  esac
}

kill_chrome() {
  echo "Killing existing Chrome processes..."
  case "$1" in
    windows) taskkill //F //IM chrome.exe 2>/dev/null || true ;;
    macos)   killall "Google Chrome" 2>/dev/null || true ;;
    linux)   pkill -f chrome 2>/dev/null || true ;;
  esac
  sleep 2
}

launch_chrome() {
  echo "Launching Chrome with remote debugging on port $CDP_PORT..."
  case "$1" in
    windows)
      local chrome_path="C:/Program Files/Google/Chrome/Application/chrome.exe"
      local user_data="C:/chrome-dev-profile"
      "$chrome_path" --remote-debugging-port=$CDP_PORT \
        --user-data-dir="$user_data" \
        --no-first-run --no-default-browser-check &
      ;;
    macos)
      /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
        --remote-debugging-port=$CDP_PORT \
        --user-data-dir="/tmp/chrome-dev-profile" \
        --no-first-run --no-default-browser-check &
      ;;
    linux)
      google-chrome \
        --remote-debugging-port=$CDP_PORT \
        --user-data-dir="/tmp/chrome-dev-profile" \
        --no-first-run --no-default-browser-check &
      ;;
  esac
  sleep 3
}

verify_cdp() {
  echo "Verifying CDP connection..."
  local response
  response=$(curl -s "http://127.0.0.1:$CDP_PORT/json/version" 2>/dev/null || echo "")
  if [ -z "$response" ]; then
    echo "ERROR: Cannot connect to CDP on port $CDP_PORT"
    exit 1
  fi
  echo "CDP active: $response"
}

configure_hermes() {
  echo "Configuring Hermes..."
  hermes config set browser.cdp_url "http://127.0.0.1:$CDP_PORT"
  hermes config set browser.headed true
  hermes config set approvals.mode smart
  hermes config set approvals.destructive_slash_confirm true
  echo "Hermes configured successfully."
}

main() {
  local os
  os=$(detect_os)
  echo "Detected OS: $os"

  if [ "$os" = "unknown" ]; then
    echo "ERROR: Unsupported OS"
    exit 1
  fi

  kill_chrome "$os"
  launch_chrome "$os"
  verify_cdp
  configure_hermes

  echo ""
  echo "=== Setup Complete ==="
  echo "Chrome is running with CDP on port $CDP_PORT"
  echo "Hermes is configured to use your real Chrome"
  echo "Open Hermes and navigate to any site — bot detection is bypassed"
}

main "$@"
