#!/bin/bash
# Root side of the ROS 2 installer, run through pkexec.
# Only ever installs ROS 2 packages: every name is checked before apt sees it.
#   helper.sh check   PKG...   validate names only (no root needed)
#   helper.sh install PKG...   add the ROS 2 repo if missing, then apt install
set -euo pipefail

valid() { [[ $1 =~ ^ros-(humble|jazzy|lyrical)-[a-z0-9][a-z0-9-]*$ || $1 == ros-dev-tools ]]; }
step() { echo "##STEP $*"; }

cmd=${1:-}
shift || true
[[ $cmd == check || $cmd == install ]] && [ $# -gt 0 ] || { echo "usage: $0 check|install PKG..." >&2; exit 2; }
for p in "$@"; do valid "$p" || { echo "Refusing package name: $p" >&2; exit 2; }; done
[ "$cmd" = check ] && { echo ok; exit 0; }
[ "$(id -u)" = 0 ] || { echo "install must run as root (through pkexec)" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt=(apt-get -y -o DPkg::Lock::Timeout=120)   # wait for other apt users instead of failing

if ! grep -Rqs packages.ros.org /etc/apt/sources.list /etc/apt/sources.list.d; then
  step "Adding the ROS 2 package repository"
  "${apt[@]}" install software-properties-common curl
  add-apt-repository -y universe
  ver=$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest |
        grep -F '"tag_name"' | awk -F'"' '{print $4}')
  [ -n "$ver" ] || { echo "Could not reach GitHub to find the ROS 2 repository package." >&2; exit 1; }
  codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
  deb=$(mktemp --suffix=.deb)
  curl -fsSL -o "$deb" "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ver}/ros2-apt-source_${ver}.${codename}_all.deb"
  dpkg -i "$deb"
  rm -f "$deb"
fi

step "Updating package lists"
"${apt[@]}" update
step "Installing packages"
"${apt[@]}" install "$@"

if command -v rosdep >/dev/null && [ ! -e /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  step "Setting up rosdep"
  rosdep init
fi
step "Finishing up"
