#!/usr/bin/env python3
"""Self-check: parsers, helper name validation, and catalog vs. the live apt repo."""
import subprocess

from ros2_installer import (CATALOG, HELPER, expand, for_distro, os_release, parse_policy,
                            parse_print_uris, repo_configured, run, with_required)

POLICY = """ros-jazzy-ros-gz:
  Installed: 1.0.24
  Candidate: 1.0.24
ros-jazzy-missing:
  Installed: (none)
  Candidate: (none)
"""
assert parse_policy(POLICY) == {"ros-jazzy-ros-gz"}
assert parse_print_uris("'http://x/a.deb' a.deb 100 MD5Sum:1\n'http://x/b.deb' b.deb 23 MD5Sum:2\n") == 123
assert parse_print_uris("") == 0


def helper_accepts(*pkgs):
    return subprocess.run([str(HELPER), "check", *pkgs], capture_output=True).returncode == 0


assert helper_accepts("ros-jazzy-desktop", "ros-dev-tools", "ros-humble-ros-gz")
for bad in ["-oAPT::x=1", "vim", "ros-noetic-desktop", "ros-jazzy-x;rm", "ros-jazzy-Desktop", "ros-jazzy-"]:
    assert not helper_accepts("ros-jazzy-desktop", bad), bad



def catalog_packages(distro):
    entries = list(CATALOG["base"]["variants"].values()) + CATALOG["goals"]
    return [p for e in entries for p in expand(e["packages"], distro)]


# Every catalog package, for every distro, must pass the helper's check (catches typos),
# and every goal must have a demo command and only require goals that exist.
ids = {g["id"] for g in CATALOG["goals"]}
for distro in CATALOG["distros"].values():
    assert helper_accepts(*catalog_packages(distro)), distro
    for g in CATALOG["goals"]:
        assert for_distro(g["demo"]["command"], distro) and set(g.get("requires", [])) <= ids, g["id"]
assert [g["id"] for g in with_required(["nav2"])] == ["simulation", "nav2"]

# Every catalog package must exist in the ROS repo for this machine's distro.
distro = CATALOG["distros"].get(os_release().get("VERSION_ID"))
if distro and repo_configured():
    pkgs = catalog_packages(distro)
    missing = set(pkgs) - parse_policy(run(["apt-cache", "policy", *pkgs]))
    assert not missing, f"not in apt for {distro}: {sorted(missing)}"
    print(f"catalog OK for {distro} ({len(set(pkgs))} packages)")
else:
    print("skipped live catalog check (unsupported OS or ROS repo not added)")
print("all checks passed")
