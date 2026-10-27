#!/usr/bin/env python3
"""Self-check: parsers, helper name validation, and catalog vs. the live apt repo."""
import subprocess
import tempfile
from pathlib import Path

import ros2_installer

from ros2_installer import (CATALOG, ENV_SOURCE_LINE, HELPER, expand, for_distro, network_env, os_release,
                            parse_policy, parse_print_uris, parse_search, parse_status, read_env, repo_configured, run, with_required,
                            write_env)

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
assert parse_search("ros-jazzy-joy - ROS 2 driver - joysticks\nros-jazzy-joy-dbgsym - debug symbols\n") == {
    "ros-jazzy-joy": "ROS 2 driver - joysticks"}
# apt Status-Fd lines (dlstatus captured from a real download; pmstatus as apt documents it)
assert parse_status("dlstatus:3:67.6379:Retrieving file 3 of 4\n") == ("dl", 67.6379, "Retrieving file 3 of 4")
assert parse_status("pmstatus:ros-jazzy-joy:40:Installing ros-jazzy-joy (amd64)") == ("pm", 40.0, "Installing ros-jazzy-joy (amd64)")
assert parse_status("pmstatus:libfoo:amd64:12.5:Unpacking libfoo:amd64") == ("pm", 12.5, "Unpacking libfoo:amd64")
assert parse_status("Setting up ros-jazzy-joy ...") is None


def helper_accepts(*pkgs):
    return subprocess.run([str(HELPER), "check", *pkgs], capture_output=True).returncode == 0


assert helper_accepts("ros-jazzy-desktop", "ros-dev-tools", "ros-humble-ros-gz")
for bad in ["-oAPT::x=1", "vim", "ros-noetic-desktop", "ros-jazzy-x;rm", "ros-jazzy-Desktop", "ros-jazzy-"]:
    assert not helper_accepts("ros-jazzy-desktop", bad), bad



def catalog_packages(distro):
    entries = list(CATALOG["base"]["variants"].values()) + CATALOG["goals"]
    rmw = [o["package"].format(distro=distro) for o in CATALOG["network"]["rmw"] if "package" in o]
    return [p for e in entries for p in expand(e["packages"], distro)] + rmw


# Every catalog package, for every distro, must pass the helper's check (catches typos),
# every demo has a command, and goals only point at goals and categories that exist.
ids = {g["id"] for g in CATALOG["goals"]}
categories = {c["id"] for c in CATALOG["categories"]}
for distro in CATALOG["distros"].values():
    assert helper_accepts(*catalog_packages(distro)), distro
    for g in CATALOG["goals"]:
        assert "demo" not in g or for_distro(g["demo"]["command"], distro), g["id"]
        assert set(g.get("requires", [])) <= ids and g["category"] in categories, g["id"]
assert [g["id"] for g in with_required(["nav2"])] == ["simulation", "nav2"]

# Network settings: defaults write nothing; localhost uses the right variable per distro;
# what we write reads back the same.
assert network_env("jazzy", 0, "default", False) == {}
assert network_env("humble", 7, "rmw_cyclonedds_cpp", True) == {
    "ROS_DOMAIN_ID": "7", "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp", "ROS_LOCALHOST_ONLY": "1"}
assert network_env("lyrical", 0, "default", True) == {"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"}
with tempfile.TemporaryDirectory() as tmp:
    ros2_installer.ENV_FILE = Path(tmp) / "sub" / "env.sh"
    env = network_env("jazzy", 42, "rmw_zenoh_cpp", True)
    write_env(env)
    assert read_env() == env
    out = subprocess.run(["bash", "-c", f"HOME={tmp}; mkdir -p $HOME/.config/ros2-gui-installer; "
                          f"cp {ros2_installer.ENV_FILE} $HOME/.config/ros2-gui-installer/; "
                          f"{ENV_SOURCE_LINE}; echo $ROS_DOMAIN_ID $RMW_IMPLEMENTATION"],
                         capture_output=True, text=True).stdout.split()
    assert out == ["42", "rmw_zenoh_cpp"], out

# Every catalog package must exist in the ROS repo for this machine's distro.
distro = CATALOG["distros"].get(os_release().get("VERSION_ID"))
# Goals marked not_released_for this distro must still be missing something, so the
# marker gets removed once the packages are released.
if distro and repo_configured():
    pkgs = catalog_packages(distro)
    missing = set(pkgs) - parse_policy(run(["apt-cache", "policy", *pkgs]))
    for g in CATALOG["goals"]:
        if distro in g.get("not_released_for", []):
            own = set(expand(g["packages"], distro))
            assert own & missing, f"{g['id']} is now released for {distro}: remove not_released_for"
            missing -= own
    assert not missing, f"not in apt for {distro}: {sorted(missing)}"
    print(f"catalog OK for {distro} ({len(set(pkgs))} packages)")
else:
    print("skipped live catalog check (unsupported OS or ROS repo not added)")
print("all checks passed")
