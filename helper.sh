#!/bin/bash
# Root side of the ROS 2 installer, run through pkexec.
# Only ever installs ROS 2 packages: every name is checked before apt sees it.
#   helper.sh check   PKG... [--upgrade PKG...]   validate names only (no root needed)
#   helper.sh install PKG... [--upgrade PKG...]   add the ROS 2 repo if missing, apt install the first
#                                                 list, then update the second with --only-upgrade
set -euo pipefail

valid() { [[ $1 =~ ^ros-(humble|jazzy|lyrical)-[a-z0-9][a-z0-9-]*$ || $1 == ros-dev-tools ]]; }
step() { echo "##STEP $*"; }

cmd=${1:-}
shift || true
[[ $cmd == check || $cmd == install ]] && [ $# -gt 0 ] || { echo "usage: $0 check|install PKG... [--upgrade PKG...]" >&2; exit 2; }
new=() upgrade=() into=new   # names after --upgrade are updates
for p in "$@"; do
  if [ "$p" = --upgrade ]; then into=upgrade; continue; fi
  valid "$p" || { echo "Refusing package name: $p" >&2; exit 2; }
  if [ "$into" = upgrade ]; then upgrade+=("$p"); else new+=("$p"); fi
done
[ "$cmd" = check ] && { echo ok; exit 0; }
[ "$(id -u)" = 0 ] || { echo "install must run as root (through pkexec)" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
# Wait for other apt users instead of failing: on a fresh Ubuntu, its own automatic updates
# (unattended-upgrades) often hold the lock for several minutes.
apt=(apt-get -y -o DPkg::Lock::Timeout=900)

# The ros2-apt-source package sets up the ROS 2 repository and its signing key. It's the only
# check that counts: older setups (ros2.list plus a signing key that expired in 2025) get it too.
if ! dpkg -s ros2-apt-source >/dev/null 2>&1; then
  step "Connecting Ubuntu to the ROS 2 download server"
  "${apt[@]}" install software-properties-common curl
  # Ubuntu-based systems like Linux Mint already have universe and their own add-apt-repository
  add-apt-repository -y universe || echo "Couldn't enable universe; continuing (it's usually already on)."
  # releases/latest redirects to .../releases/tag/<version> with no rate limit (a classroom shares
  # one IP); GitHub's API allows 60 requests an hour, so it's only the fallback.
  src=https://github.com/ros-infrastructure/ros-apt-source
  ver=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$src/releases/latest" | sed -n 's#.*/releases/tag/##p')
  [ -n "$ver" ] || ver=$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest |
                         grep -F '"tag_name"' | awk -F'"' '{print $4}')
  [[ $ver =~ ^[0-9][0-9.]*$ ]] || { echo "Could not reach GitHub to find the ROS 2 repository package." >&2; exit 1; }
  codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
  deb=$(mktemp --suffix=.deb)
  curl -fsSL -o "$deb" "$src/releases/download/${ver}/ros2-apt-source_${ver}.${codename}_all.deb"
  # Root installs this file, so make sure it really is the ROS 2 repository package.
  [ "$(dpkg-deb -f "$deb" Package 2>/dev/null)" = ros2-apt-source ] ||
    { echo "The downloaded file isn't the ROS 2 repository package; stopping." >&2; rm -f "$deb"; exit 1; }
  dpkg -i "$deb"
  rm -f "$deb"
fi
# Old-style ROS 2 sources clash with the package's key, so switch them off (renamed, not deleted).
for f in /etc/apt/sources.list.d/*.list; do
  # Only the main ROS 2 repository: leave ros2-testing and other repositories alone.
  if [ -f "$f" ] && grep -q '^[^#]*packages\.ros\.org/ros2/ubuntu' "$f"; then
    mv "$f" "$f.disabled-by-ros2-installer"
    echo "Turned off the old ROS 2 source $f (now $f.disabled-by-ros2-installer)"
  fi
done

step "Refreshing the list of available software"
# A broken third-party repository (an old PPA, an expired key) makes this fail even when the ROS
# lists arrive fine; carry on, and let the install itself fail if the ROS lists are really missing.
"${apt[@]}" update || echo "Some package lists couldn't be refreshed (see above); carrying on with the ones that worked."
# dlstatus:/pmstatus: lines from APT::Status-Fd drive the GUI progress bar
if [ ${#new[@]} -gt 0 ]; then
  step "Installing packages"
  "${apt[@]}" -o APT::Status-Fd=1 install "${new[@]}"
fi
if [ ${#upgrade[@]} -gt 0 ]; then
  step "Updating ROS 2 packages"
  "${apt[@]}" -o APT::Status-Fd=1 install --only-upgrade "${upgrade[@]}"
fi

if command -v rosdep >/dev/null && [ ! -e /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  step "Setting up rosdep, the tool that installs what ROS 2 projects need"
  rosdep init
fi
step "Finishing up"
