#!/bin/bash
# Builds dist/ros2-gui-installer_<version>_all.deb from this repo. Needs only dpkg-deb.
#   packaging/build-deb.sh [output-dir]
# The .deb puts the root helper in /usr/lib (root-owned, so user programs can't edit it) and adds a
# polkit action, so the password prompt says what it's for.
set -euo pipefail
umask 022  # packaged files must not be group-writable, whatever the builder's umask

root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$root/ros2_installer.py")
[ -n "$version" ] || { echo "Couldn't read __version__ from ros2_installer.py" >&2; exit 1; }
app_id=com.runtimerobotics.Ros2Installer
out=${1:-$root/dist}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
chmod 755 "$stage"  # mktemp makes it private; this becomes the package's /

lib=$stage/usr/lib/ros2-gui-installer
share=$stage/usr/share
install -d "$lib/data" "$stage/usr/bin" "$share/applications" "$share/icons/hicolor/scalable/apps" \
           "$share/polkit-1/actions" "$share/doc/ros2-gui-installer" "$stage/DEBIAN" "$out"
install -m 755 "$root/ros2_installer.py" "$root/helper.sh" "$lib/"
install -m 644 "$root/catalog.yaml" "$lib/"
install -m 644 "$root/data/$app_id.svg" "$lib/data/"
ln -s ../lib/ros2-gui-installer/ros2_installer.py "$stage/usr/bin/ros2-gui-installer"
install -m 644 "$root/data/$app_id.svg" "$share/icons/hicolor/scalable/apps/"
install -m 644 "$root/data/$app_id.policy" "$share/polkit-1/actions/"

cat > "$share/applications/$app_id.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ROS 2 Installer
Comment=Install ROS 2, simulation, Nav2, MoveIt 2 and more
Exec=/usr/bin/ros2-gui-installer
Icon=$app_id
Terminal=false
Categories=Development;
DESKTOP

cat > "$share/doc/ros2-gui-installer/copyright" <<COPYRIGHT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ros2_gui_installer
Source: https://github.com/runtimerobotics/ros2_gui_installer

Files: *
Copyright: 2026 Lentin Joseph, Runtime Robotics
License: Apache-2.0
 On Debian and Ubuntu systems, the full text of the Apache License 2.0
 is in /usr/share/common-licenses/Apache-2.0.
COPYRIGHT

# Debian wants a changelog and a manual page; the release notes on GitHub are the real changelog.
when=$(date -R -d "@$(git -C "$root" log -1 --format=%ct 2>/dev/null || date +%s)")
gzip -9n > "$share/doc/ros2-gui-installer/changelog.gz" <<CHANGELOG
ros2-gui-installer ($version) unstable; urgency=medium

  * Release notes for v$version:
    https://github.com/runtimerobotics/ros2_gui_installer/releases

 -- Runtime Robotics <runtimerobotics@gmail.com>  $when
CHANGELOG

install -d "$share/man/man1"
gzip -9n > "$share/man/man1/ros2-gui-installer.1.gz" <<MANPAGE
.TH ROS2-GUI-INSTALLER 1 "" "$version" "User Commands"
.SH NAME
ros2-gui-installer \- beginner-friendly installer for ROS 2
.SH SYNOPSIS
.B ros2-gui-installer
[\fB\-\-version\fR | \fB\-\-check\-updates\fR]
.SH DESCRIPTION
Opens a window that checks the computer, lets you pick parts of ROS 2 to install
(simulation, Nav2, MoveIt 2 and more), and installs them with one password prompt.
Run it as your normal user, not with sudo.
.SH OPTIONS
.TP
.B \-\-version
Print the version and exit.
.TP
.B \-\-check\-updates
Show a desktop notification if installed ROS 2 packages have updates waiting.
The daily update timer runs this.
.SH SEE ALSO
https://github.com/runtimerobotics/ros2_gui_installer
MANPAGE

cat > "$stage/DEBIAN/control" <<CONTROL
Package: ros2-gui-installer
Version: $version
Architecture: all
Maintainer: Runtime Robotics <runtimerobotics@gmail.com>
Installed-Size: $(du -sk --exclude=DEBIAN "$stage" | cut -f1)
Depends: python3, python3-gi, python3-yaml, gir1.2-gtk-4.0, gir1.2-adw-1, librsvg2-common, pkexec | policykit-1, curl
Recommends: libnotify-bin
Section: devel
Priority: optional
Homepage: https://github.com/runtimerobotics/ros2_gui_installer
Description: beginner-friendly installer for ROS 2
 A GTK4 wizard that installs ROS 2 (Humble, Jazzy, Lyrical) on Ubuntu and
 Ubuntu-based systems: pick categories like simulation, Nav2 or MoveIt 2,
 review the download, install with one password prompt, then try demos.
CONTROL

deb="$out/ros2-gui-installer_${version}_all.deb"
dpkg-deb --root-owner-group --build "$stage" "$deb" >/dev/null
echo "$deb"
