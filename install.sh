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
  local desktop="$HOME/.local/share/applications/ros2-gui-installer.desktop"

  [ "$(id -u)" != 0 ] || { echo "Run this as your normal user, without sudo." >&2; exit 1; }
  grep -qx 'ID=ubuntu' /etc/os-release || echo "Warning: this installer is made for Ubuntu; continuing anyway." >&2

  local deps=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml curl tar)
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

  echo "Downloading the installer to $dest"
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL "$repo/archive/refs/heads/main.tar.gz" | tar -xz --strip-components=1 -C "$tmp"
  [ -x "$tmp/ros2_installer.py" ] || { echo "Download looks wrong; nothing changed." >&2; rm -rf "$tmp"; exit 1; }
  mkdir -p "$(dirname "$dest")" "$(dirname "$bin")" "$(dirname "$desktop")"
  rm -rf "$dest"
  mv "$tmp" "$dest"

  ln -sf "$dest/ros2_installer.py" "$bin"
  cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ROS 2 Installer
Comment=Install ROS 2, simulation, Nav2, MoveIt 2 and more
Exec=$bin
Icon=system-software-install
Terminal=false
Categories=Development;Education;
EOF

  echo "Done. Open \"ROS 2 Installer\" from your app menu, or run: $bin"
  if [ -z "${NO_LAUNCH:-}" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    nohup "$bin" >/dev/null 2>&1 &
  fi
}

main "$@"
