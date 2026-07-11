#!/bin/bash
# Build a double-clickable "Content Creator.app" that launches the desktop editor
# using this project's existing virtualenv. This is a lightweight launcher bundle
# (not a PyInstaller freeze): it avoids bundling PyTorch/Whisper/ffmpeg, which are
# large and fragile to freeze, and instead reuses the venv + system tools.
#
# Usage:
#   bash editor/build_macos_app.sh            # installs to ~/Applications
#   bash editor/build_macos_app.sh /Applications
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$PROJECT_ROOT/editor/venv/bin/python"
DEST_DIR="${1:-$HOME/Applications}"
APP="$DEST_DIR/Content Creator.app"

if [ ! -x "$VENV_PY" ]; then
  echo "error: venv python not found at $VENV_PY" >&2
  exit 1
fi

echo "Building $APP ..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# --- Info.plist ---
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>Content Creator</string>
  <key>CFBundleDisplayName</key>       <string>Content Creator</string>
  <key>CFBundleIdentifier</key>        <string>com.local.content-creator</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>launcher</string>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST

# --- launcher ---
# GUI apps launched from Finder DON'T inherit your shell PATH, so ffmpeg/exiftool
# (typically in Homebrew dirs) would be invisible. Prepend the common locations.
cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$PROJECT_ROOT"
exec "$VENV_PY" "$PROJECT_ROOT/editor/desktop.py" >> "\$HOME/Library/Logs/content-creator.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

echo "Done. Launch it from Spotlight (⌘-Space → \"Content Creator\") or:"
echo "  open \"$APP\""
