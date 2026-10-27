#!/bin/bash
# Installs (or updates) the ROS 2 GUI Installer for the current user, then opens it:
#   curl -fsSL https://raw.githubusercontent.com/runtimerobotics/ros2_gui_installer/main/install.sh | bash
# Set NO_LAUNCH=1 to skip opening the app at the end.
set -euo pipefail

# Everything lives in main() so bash reads the whole script before running it;
# otherwise apt/sudo reading stdin could swallow the rest of a piped script.
main() {
  local repo=https://github.com/runtimerobotics/ros2_gui_installer
  local dest="$HOME/.local/share/ros2-gui-installer"
  local bin="$HOME/.local/bin/ros2-gui-installer"
  local app_id=com.runtimerobotics.Ros2Installer
  local desktop="$HOME/.local/share/applications/$app_id.desktop"
  local icon="$HOME/.local/share/icons/hicolor/scalable/apps/$app_id.svg"

  [ "$(id -u)" != 0 ] || { echo "Run this as your normal user, without sudo." >&2; exit 1; }
  grep -q '^UBUNTU_CODENAME=' /etc/os-release || grep -qx 'ID=ubuntu' /etc/os-release ||
    echo "Warning: this installer is made for Ubuntu and Ubuntu-based systems; continuing anyway." >&2

  local deps=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml librsvg2-common curl tar)  # librsvg2-common: SVG icons
  local missing=() p
  for p in "${deps[@]}"; do
    dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii' || missing+=("$p")
  done
  # pkexec is its own package from 24.04 on; on 22.04 it comes in policykit-1
  if ! command -v pkexec >/dev/null; then
    if apt-cache show pkexec >/dev/null 2>&1; then missing+=(pkexec); else missing+=(policykit-1); fi
  fi
  if [ ${#missing[@]} -gt 0 ]; then
    echo "Installing what the GUI needs: ${missing[*]} (asks for your password)"
    sudo apt-get update < /dev/null
    sudo apt-get install -y "${missing[@]}" < /dev/null
  fi

  # The latest release, not main. releases/latest redirects to .../releases/tag/<tag> and has no rate
  # limit (good for classrooms behind one IP), but GitHub can lag after a new release; then ask the API.
  local tag
  tag=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$repo/releases/latest" | sed -n 's#.*/releases/tag/##p')
  [ -n "$tag" ] || tag=$(curl -fsSL "https://api.github.com/repos/${repo#https://github.com/}/releases/latest" |
                         grep -F '"tag_name"' | awk -F'"' '{print $4}')
  [ -n "$tag" ] || { echo "Couldn't find a release of the installer on GitHub. Check your internet connection." >&2; exit 1; }
  echo "Downloading ROS 2 Installer $tag to $dest"
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL "$repo/archive/refs/tags/$tag.tar.gz" | tar -xz --strip-components=1 -C "$tmp"
  [ -x "$tmp/ros2_installer.py" ] || { echo "Download looks wrong; nothing changed." >&2; rm -rf "$tmp"; exit 1; }
  mkdir -p "$(dirname "$dest")" "$(dirname "$bin")" "$(dirname "$desktop")" "$(dirname "$icon")"
  rm -rf "$dest"
  mv "$tmp" "$dest"

  ln -sf "$dest/ros2_installer.py" "$bin"
  cp "$dest/data/$app_id.svg" "$icon"
  rm -f "$HOME/.local/share/applications/ros2-gui-installer.desktop"  # menu entry name before 0.1.0
  cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ROS 2 Installer
Comment=Install ROS 2, simulation, Nav2, MoveIt 2 and more
Exec=$bin
Icon=$app_id
Terminal=false
Categories=Development;
EOF

  echo "Done. Open \"ROS 2 Installer\" from your app menu, or run: $bin"
  if [ -z "${NO_LAUNCH:-}" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    nohup "$bin" >/dev/null 2>&1 &
  fi
}

main "$@"
