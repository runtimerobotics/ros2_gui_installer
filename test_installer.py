#!/usr/bin/env python3
"""Self-check: parsers, helper name validation, and catalog vs. the live apt repo."""
import re
import subprocess
import tempfile
from pathlib import Path

import ros2_installer

from ros2_installer import (CATALOG, ENV_SOURCE_LINE, HELPER, demo_script, detect_distro, fetch_profile, load_profile, newer_release, expand, explain_failure, for_distro, parse_lspci, terminal_script, tutorials_url, network_env,
                            parse_policy, parse_print_uris, parse_search, parse_show, parse_upgradable, unit_files, parse_status, read_env, repo_configured, run, terminal_command, usable_goals, with_required,
                            write_env)

POLICY = """ros-jazzy-ros-gz:
  Installed: 1.0.24
  Candidate: 1.0.24
ros-jazzy-missing:
  Installed: (none)
  Candidate: (none)
"""
assert re.fullmatch(r"\d+\.\d+\.\d+", ros2_installer.__version__), ros2_installer.__version__
assert parse_policy(POLICY) == {"ros-jazzy-ros-gz"}
assert parse_print_uris("'http://x/a.deb' a.deb 100 MD5Sum:1\n'http://x/b.deb' b.deb 23 MD5Sum:2\n") == 123
assert parse_print_uris("") == 0
assert parse_show("Package: ros-jazzy-joy\nVersion: 3\nDescription: Joystick driver: joy_node\n"
                  "Description-md5: ab\n\nPackage: ros-jazzy-x\nDescription-en: X tool\n") == {
    "ros-jazzy-joy": "Joystick driver: joy_node", "ros-jazzy-x": "X tool"}
# Updates: only ROS 2 packages of this distro, and only upgrades ("[old version]"), not new installs.
SIM = """Inst ros-jazzy-rviz2 [14.1.6-1noble.1] (14.1.7-1noble.2 ROS:noble [amd64])
Inst ros-jazzy-new-dep (1.0-1 ROS:noble [amd64])
Inst libfoo [1.0] (1.1 Ubuntu:24.04/noble-updates [amd64])
Inst ros-humble-x [1] (2 ROS [amd64])
Conf ros-jazzy-rviz2 (14.1.7-1noble.2 ROS:noble [amd64])
"""
assert parse_upgradable(SIM, "jazzy") == ["ros-jazzy-rviz2"]
units = unit_files()
assert "--check-updates" in units["ros2-gui-installer-updates.service"]
assert "OnCalendar=daily" in units["ros2-gui-installer-updates.timer"]
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
assert helper_accepts("ros-jazzy-desktop", "--upgrade", "ros-jazzy-rviz2")
assert not helper_accepts("ros-jazzy-desktop", "--upgrade", "openssh-server")
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
        # amd64 always has a demo; "no demo" (null) entries are only for one architecture (humble-arm64)
        assert "demo" not in g or for_distro(g["demo"]["command"], distro, "amd64"), g["id"]
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

# A section not released for a distro is never offered there, even on a fresh machine where
# apt doesn't know any ROS packages yet (avail is None).
assert "mobile" not in usable_goals("lyrical", None)
assert "mobile" in usable_goals("jazzy", None)
assert "nav2" in usable_goals("lyrical", None)  # needs Simulate, which is released
assert "mobile" not in usable_goals("jazzy", set())  # apt knows nothing is available

# Common apt failures get one plain sentence; anything else falls back to the generic message.
assert "Another program" in explain_failure("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 812")
assert "disk space" in explain_failure("E: You don't have enough free space in /var/cache/apt/archives/.")
assert "internet" in explain_failure("E: Failed to fetch http://packages.ros.org/ros2/ubuntu/pool/main/x.deb  Connection timed out")
assert "aren't available" in explain_failure("E: Unable to locate package ros-lyrical-turtlebot4-simulator")
assert explain_failure("dpkg: error processing package foo (--configure)") is None

# Graphics names from lspci -mm (a real line from an RTX 4060 laptop, an AMD card, a VM).
LSPCI = """01:00.0 "VGA compatible controller" "NVIDIA Corporation" "AD107M [GeForce RTX 4060 Max-Q / Mobile]" -ra1 -p00 "Lenovo" "AD107M"
03:00.0 "VGA compatible controller" "Advanced Micro Devices, Inc. [AMD/ATI]" "Navi 23 [Radeon RX 6600]" -rc7 "x" "y"
00:0f.0 "VGA compatible controller" "VMware" "SVGA II Adapter" "x" "y"
00:1f.3 "Audio device" "Intel Corporation" "Raptor Lake High Definition Audio Controller" "x" "y"
"""
assert parse_lspci(LSPCI) == ["NVIDIA GeForce RTX 4060 Max-Q / Mobile", "AMD Radeon RX 6600", "VMware SVGA II Adapter"]

# arm64: Humble has no Gazebo Classic there, so Nav2 and TurtleBot 3 install without their
# simulation and have no demo, but stay available (Jetson boards run Humble on arm64).
assert "ros-humble-turtlebot3-gazebo" in expand(next(g for g in CATALOG["goals"] if g["id"] == "nav2")["packages"], "humble", "amd64")
assert "ros-humble-turtlebot3-gazebo" not in expand(next(g for g in CATALOG["goals"] if g["id"] == "nav2")["packages"], "humble", "arm64")
assert for_distro(next(g for g in CATALOG["goals"] if g["id"] == "nav2")["demo"]["command"], "humble", "arm64") is None
assert {"nav2", "turtlebot3"} <= usable_goals("humble", None, "arm64")
assert "ros-base" in CATALOG["base"]["variants"] and all(d["command"] for d in CATALOG["base"]["demos"])

# Try it scripts tell beginners how to stop a demo, and survive odd titles (shell quoting).
script = demo_script("jazzy", "Robot's demo; rm -rf ~", "echo DEMO-RAN")
assert "Press Ctrl+C" in script and "source /opt/ros/jazzy/setup.bash" in script
ran = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
assert "Starting Robot's demo; rm -rf ~. Press Ctrl+C" in ran and "DEMO-RAN" in ran and "Stopped." in ran, ran
hello = subprocess.run(["bash", "-c", terminal_script("jazzy")], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL).stdout
assert "ROS 2 is ready in this terminal" in hello, hello
assert tutorials_url("lyrical") == "https://docs.ros.org/en/lyrical/First-Steps.html"

# Every page the app shows lights up a step in the step bar, and every step is used.
pages = set(re.findall(r'show_page\("(\w+)"\)', open(ros2_installer.__file__).read()))
assert pages <= set(ros2_installer.Installer.STEPS), pages - set(ros2_installer.Installer.STEPS)
assert set(ros2_installer.Installer.STEPS.values()) == set(range(len(ros2_installer.Installer.STEP_NAMES)))

# Workshop profiles: the good parts load, anything unexpected is refused with a plain reason.
good = load_profile("name: Nav2 course\nros: desktop-full\ncategories: [learn, simulate, mobile]\n"
                    "packages: ['ros-{distro}-joy']\nnetwork: {rmw: rmw_cyclonedds_cpp, localhost: true, domain_id: 7}\n")
assert good["name"] == "Nav2 course" and good["categories"] == ["learn", "simulate", "mobile"]
assert good["network"]["domain_id"] == 7 and good["terminal"] is True
assert load_profile("name: Minimal\n")["ros"] == "desktop-full"  # the recommended option by default
for bad, reason in [("- just a list", "settings like"), ("name: x\nrun: rm -rf /", "unknown setting"),
                    ("ros: desktop", "needs a name"), ("name: x\nros: everything", "ros: must be"),
                    ("name: x\ncategories: [flying]", "categories:"),
                    ("name: x\npackages: [vim]", "packages:"), ("name: x\npackages: ['ros-jazzy-x; rm -rf ~']", "packages:"),
                    ("name: x\nnetwork: {domain_id: 500}", "domain_id"), ("name: x\nnetwork: {domain_id: true}", "domain_id"),
                    ("name: x\nnetwork: {rmw: telepathy}", "rmw must"), ("name: x\nnetwork: {proxy: evil}", "can only set"),
                    ("name: x\nterminal: yes please", "true or false"), ("name: [unclosed", "valid YAML"),
                    ("name: x\npackages: [ros-{distro}-joy]", "in quotes")]:
    try:
        load_profile(bad)
        raise AssertionError(f"accepted a bad profile: {bad!r}")
    except ValueError as e:
        assert reason in str(e), (bad, str(e))
with tempfile.TemporaryDirectory() as tmp:
    big = Path(tmp) / "big.yaml"
    big.write_text("name: big\n" + "# padding\n" * 10000)
    for source, reason in [(str(big), "too big"), (str(Path(tmp) / "missing.yaml"), "couldn't read")]:
        try:
            fetch_profile(source)
            raise AssertionError(source)
        except ValueError as e:
            assert reason in str(e), str(e)

# The example profiles in profiles/ must always load.
examples = sorted(Path(ros2_installer.HERE / "profiles").glob("*.yaml"))
assert examples, "no example profiles"
for example in examples:
    load_profile(example.read_text())

# Install logs are saved with a header naming the setup.
with tempfile.TemporaryDirectory() as tmp:
    ros2_installer.LOG_DIR = Path(tmp) / "logs"
    saved = ros2_installer.save_log(ros2_installer.log_report("jazzy", "E: something broke\n"))
    text = saved.read_text()
    assert saved.parent == Path(tmp) / "logs" and saved.name.startswith("install-") and saved.suffix == ".log"
    assert text.startswith(f"ROS 2 Installer {ros2_installer.__version__} |") and "ROS 2 jazzy" in text
    assert text.endswith("E: something broke\n")

# Installer update notice: only a strictly newer release counts; offline ('') means no notice.
base = "https://github.com/runtimerobotics/ros2_gui_installer/releases"
assert newer_release("0.1.8", f"{base}/tag/v0.1.9") == "0.1.9"
assert newer_release("0.1.8", f"{base}/tag/v0.2.0") == "0.2.0"
assert newer_release("0.1.10", f"{base}/tag/v0.1.9") is None  # compared as numbers, not text
assert newer_release("0.1.8", f"{base}/tag/v0.1.8") is None
assert newer_release("0.1.8", base) is None and newer_release("0.1.8", "") is None

# Distro detection uses the Ubuntu codename, so Ubuntu-based systems map to their base release.
assert detect_distro({"ID": "ubuntu", "VERSION_ID": "24.04", "VERSION_CODENAME": "noble", "UBUNTU_CODENAME": "noble"}) == "jazzy"
assert detect_distro({"ID": "linuxmint", "VERSION_ID": "22", "VERSION_CODENAME": "wilma", "UBUNTU_CODENAME": "noble"}) == "jazzy"
assert detect_distro({"ID": "pop", "VERSION_ID": "22.04", "VERSION_CODENAME": "jammy", "UBUNTU_CODENAME": "jammy"}) == "humble"
assert detect_distro({"ID": "ubuntu", "VERSION_CODENAME": "resolute"}) == "lyrical"
assert detect_distro({"ID": "debian", "VERSION_ID": "12", "VERSION_CODENAME": "bookworm"}) is None

# Try it opens the desktop's own terminal, with that terminal's way of taking a command.
cmd = ["bash", "-c", "echo hi"]
has = lambda *names: (lambda program: program in names)
assert terminal_command(cmd, "KDE", has("gnome-terminal", "konsole")) == ["konsole", "-e", *cmd]
assert terminal_command(cmd, "ubuntu:GNOME", has("gnome-terminal", "konsole")) == ["gnome-terminal", "--", *cmd]
assert terminal_command(cmd, "XFCE", has("xfce4-terminal")) == ["xfce4-terminal", "-x", *cmd]
assert terminal_command(cmd, "LXQt", has("qterminal")) == ["qterminal", "-e", "bash -c 'echo hi'"]
assert terminal_command(cmd, "i3", has()) == ["x-terminal-emulator", "-e", *cmd]

# Every catalog package must exist in the ROS repo for this machine's distro.
distro = detect_distro()
# Goals marked not_released_for this distro must still be missing something, so the
# marker gets removed once the packages are released.
if distro and repo_configured():
    pkgs = catalog_packages(distro)
    missing = set(pkgs) - parse_policy(run(["apt-cache", "policy", *pkgs]))
    for g in CATALOG["goals"]:
        if {distro, f"{distro}-{ros2_installer.ARCH}"} & set(g.get("not_released_for", [])):
            own = set(expand(g["packages"], distro))
            assert own & missing, f"{g['id']} is now released for {distro}: remove not_released_for"
            missing -= own
    assert not missing, f"not in apt for {distro}: {sorted(missing)}"
    print(f"catalog OK for {distro} ({len(set(pkgs))} packages)")
else:
    print("skipped live catalog check (unsupported OS or ROS repo not added)")
print("all checks passed")
