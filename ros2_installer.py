#!/usr/bin/env python3
"""ROS 2 GUI installer: system check -> choose -> review -> install -> try it.

Runs as the normal user; only helper.sh runs as root (through pkexec).
Uses GTK4 + libadwaita 1.1 widgets only, so it works on Ubuntu 22.04 too.
"""
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import yaml
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

HERE = Path(__file__).resolve().parent
HELPER = HERE / "helper.sh"
CATALOG = yaml.safe_load((HERE / "catalog.yaml").read_text())
__version__ = "0.2.5"  # bump with each GitHub release; the tag is v<version>
REPO_URL = "https://github.com/runtimerobotics/ros2_gui_installer"
APP_ID = "com.runtimerobotics.Ros2Installer"  # also the icon and .desktop file name
MIN_FREE_GB = 10  # Desktop-Full + Gazebo is ~6-8 GB once unpacked
MIN_RAM_GB = 4  # below this Gazebo and RViz struggle
PROFILE_SOURCE = None  # --profile URL or file, set by main()
LOG_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "ros2-gui-installer"
ENV_FILE = Path.home() / ".config" / "ros2-gui-installer" / "env.sh"
ENV_SOURCE_LINE = '[ -f "$HOME/.config/ros2-gui-installer/env.sh" ] && source "$HOME/.config/ros2-gui-installer/env.sh"'


def run(cmd):
    """Run a read-only command and return its stdout ('' if the tool is missing)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


ARCH = run(["dpkg", "--print-architecture"]).strip() or "unknown"
SUPPORTED_ARCHS = ("amd64", "arm64")  # what packages.ros.org builds ROS 2 for; armhf has none


def os_release():
    pairs = (line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    return {k: v.strip('"') for k, v in pairs}


def for_distro(value, distro, arch=None):
    """Catalog values are either shared, or a {default: ..., <distro>: ..., <distro>-<arch>: ...} map."""
    if not isinstance(value, dict):
        return value
    for key in (f"{distro}-{arch or ARCH}", distro):
        if key in value:
            return value[key]
    return value["default"]


def detect_distro(osr=None):
    """ROS 2 distro for this system, or None. Uses the Ubuntu codename, so Ubuntu-based
    systems (Linux Mint, Pop!_OS) map to the Ubuntu release they're built on."""
    osr = osr or os_release()
    return CATALOG["distros"].get(osr.get("UBUNTU_CODENAME") or osr.get("VERSION_CODENAME"))


def expand(pkgs, distro, arch=None):
    return [p.format(distro=distro) for p in for_distro(pkgs, distro, arch)]


def with_required(goal_ids):
    """goal_ids plus every goal they require (transitively), in catalog order."""
    by_id = {g["id"]: g for g in CATALOG["goals"]}
    todo, wanted = list(goal_ids), set()
    while todo:
        gid = todo.pop()
        if gid not in wanted:
            wanted.add(gid)
            todo += by_id[gid].get("requires", [])
    return [g for g in CATALOG["goals"] if g["id"] in wanted]


RELEASE_TAG = re.compile(r"/releases/tag/v?(\d+(?:\.\d+)*)$")


def newer_release(current, latest_url):
    """The latest version if it's newer than current, read from a releases/latest redirect URL; else None."""
    m = RELEASE_TAG.search(latest_url or "")
    as_tuple = lambda v: tuple(int(x) for x in v.split("."))  # noqa: E731
    return m[1] if m and as_tuple(m[1]) > as_tuple(current) else None


def latest_release_url():
    """Where GitHub's releases/latest redirects to ('' when offline). No rate limit, unlike the API."""
    try:
        request = urllib.request.Request(f"{REPO_URL}/releases/latest", method="HEAD")
        return urllib.request.urlopen(request, timeout=5).geturl()
    except OSError:
        return ""


def usable_goals(distro, avail, arch=None):
    """Ids of sections that can be installed: everything they need is released for this distro.
    Before the ROS repo is added apt knows no ROS packages (avail is None), so the catalog's
    not_released_for list is the only guide then."""
    def ok(g):
        chain = with_required([g["id"]])
        blocked = {distro, f"{distro}-{arch or ARCH}"}
        if any(blocked & set(r.get("not_released_for", [])) for r in chain):
            return False
        return avail is None or {p for r in chain for p in expand(r["packages"], distro, arch)} <= avail
    return {g["id"] for g in CATALOG["goals"] if ok(g)}


def installed(pkgs):
    out = run(["dpkg-query", "-W", "-f=${Package} ${db:Status-Abbrev}\n", *pkgs])
    return {f[0] for f in map(str.split, out.splitlines()) if f[1:2] == ["ii"]}


def parse_policy(text):
    """Packages in `apt-cache policy` output that have an install candidate."""
    found, pkg = set(), None
    for line in text.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            pkg = line[:-1]
        elif line.strip().startswith("Candidate:") and pkg and "(none)" not in line:
            found.add(pkg)
    return found


def parse_search(text):
    """{package: description} from `apt-cache search` output, without debug-symbol packages."""
    pairs = (line.split(" - ", 1) for line in text.splitlines() if " - " in line)
    return {name: desc for name, desc in pairs if not name.endswith("-dbgsym")}


def parse_show(text):
    """{package: one-line description} from `apt-cache show` output."""
    out, pkg = {}, None
    for line in text.splitlines():
        if line.startswith("Package: "):
            pkg = line[9:]
        elif line.startswith(("Description: ", "Description-en: ")) and pkg:
            out[pkg] = line.split(": ", 1)[1]
    return out


def parse_print_uris(text):
    """Total download bytes from `apt-get --print-uris -qq install` output."""
    return sum(int(line.split()[2]) for line in text.splitlines() if line.startswith("'"))


STATUS_LINE = re.compile(r"^(dl|pm)status:.*?:(\d+(?:\.\d+)?):(.*)$")


def parse_status(line):
    """(kind 'dl'|'pm', percent, description) from an apt Status-Fd line, else None."""
    m = STATUS_LINE.match(line.rstrip("\n"))
    return (m[1], float(m[2]), m[3]) if m else None


def parse_upgradable(text, distro):
    """ROS 2 packages with an update waiting, from `apt-get -s upgrade` output ("Inst pkg [old] (new …)")."""
    return re.findall(rf"^Inst (ros-{distro}-[a-z0-9-]+) \[", text, re.M)


def upgradable(distro):
    # Uses the package lists from Ubuntu's daily apt update, so it needs no password.
    return parse_upgradable(run(["apt-get", "-s", "--with-new-pkgs", "upgrade"]), distro)


UNIT = "ros2-gui-installer-updates"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def unit_files():
    """{filename: contents} for the daily update check (a systemd user timer)."""
    return {
        f"{UNIT}.service": "[Unit]\nDescription=Check for ROS 2 package updates\n\n[Service]\nType=oneshot\n"
                           f'ExecStart="{sys.executable}" "{HERE / "ros2_installer.py"}" --check-updates\n',
        f"{UNIT}.timer": "[Unit]\nDescription=Daily check for ROS 2 package updates\n\n[Timer]\n"
                         "OnCalendar=daily\nPersistent=true\nRandomizedDelaySec=1h\n\n[Install]\nWantedBy=timers.target\n",
    }


def update_notifications_on():
    return (UNIT_DIR / f"{UNIT}.timer").exists()


def set_update_notifications(on):
    """Turn the daily update notification on or off. Returns False if systemd refused."""
    systemctl = ["systemctl", "--user"]
    if on:
        UNIT_DIR.mkdir(parents=True, exist_ok=True)
        for name, text in unit_files().items():
            (UNIT_DIR / name).write_text(text)
        steps = [systemctl + ["daemon-reload"], systemctl + ["enable", "--now", f"{UNIT}.timer"]]
    else:
        steps = [systemctl + ["disable", "--now", f"{UNIT}.timer"]]
    ok = all(subprocess.run(s, capture_output=True).returncode == 0 for s in steps)
    if not on:
        for name in unit_files():
            (UNIT_DIR / name).unlink(missing_ok=True)
        subprocess.run(systemctl + ["daemon-reload"], capture_output=True)
    return ok


def check_updates():
    """`--check-updates`, run by the daily timer: a desktop notification when ROS 2 updates are waiting."""
    distro = detect_distro()
    pkgs = upgradable(distro) if distro else []
    print(f"{len(pkgs)} ROS 2 package updates ready")
    if pkgs and shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=ROS 2 Installer", f"--icon={APP_ID}",
                        "ROS 2 updates are ready",
                        f"{len(pkgs)} ROS 2 package{'s' * (len(pkgs) > 1)} can be updated. "
                        "Open ROS 2 Installer to update."])


def repo_configured():
    """True once apt has package lists from the ROS 2 repository."""
    return "packages.ros.org" in run(["apt-cache", "policy"])


def plan_install(pkgs):
    """(packages apt would set up, download bytes), or None if apt can't resolve them."""
    sim = subprocess.run(["apt-get", "-s", "install", *pkgs], capture_output=True, text=True)
    if sim.returncode:
        return None
    count = sum(line.startswith("Inst ") for line in sim.stdout.splitlines())
    return count, parse_print_uris(run(["apt-get", "--print-uris", "-qq", "install", *pkgs]))


# (text apt prints, what to tell a beginner). The first match wins.
FAILURE_REASONS = [
    (("Could not get lock", "Unable to acquire the dpkg frontend lock", "Waiting for cache lock"),
     "Another program is still installing software (often Ubuntu's own updates). "
     "Wait a few minutes or restart the computer, then press Try again."),
    (("enough free space", "No space left on device"),
     "There isn't enough free disk space. Free some up, then press Try again."),
    (("Failed to fetch", "Temporary failure resolving", "Could not resolve", "Connection failed"),
     "The download was interrupted. Check your internet connection, then press Try again."),
    (("Unable to locate package", "has no installation candidate"),
     "Some packages aren't available for your ROS 2 version. Go back, untick them, and try again."),
]


def explain_failure(log):
    """One plain sentence for a common apt failure in the log, or None."""
    for needles, reason in FAILURE_REASONS:
        if any(n in log for n in needles):
            return reason
    return None


def log_report(distro, log):
    """The install log with a header that says which setup it came from, for sharing when asking for help."""
    header = (f"ROS 2 Installer {__version__} | {os_release().get('PRETTY_NAME', 'unknown system')} | "
              f"{ARCH} | ROS 2 {distro}\n")
    return header + log


def save_log(text):
    """Keep every install's log in ~/.local/state/ros2-gui-installer/ and return its path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"install-{time.strftime('%Y%m%d-%H%M%S')}.log"
    path.write_text(text)
    return path


PROFILE_KEYS = {"name", "description", "distro", "ros", "categories", "packages", "network", "terminal"}
PROFILE_PACKAGE = re.compile(r"^ros-(\{distro\}|humble|jazzy|lyrical)-[a-z0-9][a-z0-9-]*$")
PROFILE_MAX_BYTES = 64 * 1024


def load_profile(text):
    """A workshop profile (YAML) checked against the catalog. Raises ValueError with a plain reason.
    Profiles can come from any URL, so only known settings and ROS 2 package names get through."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        hint = ". Put names with {distro} in quotes, or list one per line" if "{distro}" in text else ""
        raise ValueError(f"it isn't valid YAML ({getattr(e, 'problem', e)}){hint}") from None
    if not isinstance(data, dict):
        raise ValueError("it should be settings like name: and categories:")
    if set(data) - PROFILE_KEYS:
        raise ValueError(f"unknown setting: {', '.join(sorted(map(str, set(data) - PROFILE_KEYS)))}")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("it needs a name:")
    distro = data.get("distro")
    if distro is not None and distro not in CATALOG["distros"].values():
        raise ValueError(f"distro: must be one of {', '.join(CATALOG['distros'].values())}")
    default = next(k for k, v in CATALOG["base"]["variants"].items() if v.get("default"))
    ros = data.get("ros", default)
    if ros not in CATALOG["base"]["variants"]:
        raise ValueError(f"ros: must be one of {', '.join(CATALOG['base']['variants'])}")
    categories = data.get("categories", [])
    known = {c["id"] for c in CATALOG["categories"]}
    if not isinstance(categories, list) or not set(map(str, categories)) <= known:
        raise ValueError(f"categories: must be a list of {', '.join(sorted(known))}")
    packages = data.get("packages", [])
    if not isinstance(packages, list) or not all(isinstance(p, str) and PROFILE_PACKAGE.match(p) for p in packages):
        raise ValueError("packages: must be a list of ROS 2 package names like ros-{distro}-joy")
    network = data.get("network", {})
    rmw_ids = [o["id"] for o in CATALOG["network"]["rmw"]]
    if not isinstance(network, dict) or set(network) - {"domain_id", "rmw", "localhost"}:
        raise ValueError("network: can only set domain_id, rmw and localhost")
    domain = network.get("domain_id", 0)
    if type(domain) is not int or not 0 <= domain <= 101:
        raise ValueError("network: domain_id must be a whole number from 0 to 101")
    if network.get("rmw", "default") not in rmw_ids:
        raise ValueError(f"network: rmw must be one of {', '.join(rmw_ids)}")
    if type(network.get("localhost", False)) is not bool or type(data.get("terminal", True)) is not bool:
        raise ValueError("network: localhost and terminal: must be true or false")
    return {"name": name.strip(), "description": str(data.get("description", "")).strip(), "distro": distro, "ros": ros,
            "categories": categories, "packages": packages, "network": network,
            "terminal": data.get("terminal", True)}


def profile_text(name, ros, categories, packages, network, terminal, description="", distro=None):
    """A workshop profile as YAML, written so load_profile() reads it back unchanged."""
    data = {"name": name}
    if description:
        data["description"] = description
    if distro:
        data["distro"] = distro  # profiles are made for one ROS 2 version
    data.update(ros=ros, categories=categories)
    if packages:
        data["packages"] = packages
    if network:
        data["network"] = network
    data["terminal"] = terminal
    return (f"# ROS 2 Installer workshop profile, saved by version {__version__}.\n"
            "# Open it with:  ros2-gui-installer --profile <this file or its URL>\n"
            + yaml.safe_dump(data, sort_keys=False))


def extra_packages(distro, manual, covered, deps):
    """ROS 2 packages installed by hand (apt-mark showmanual) that no catalog choice or ROS 2 option brings in."""
    return sorted(p for p in manual if p.startswith(f"ros-{distro}-") and p not in covered and p not in deps)


def fetch_profile(source):
    """The text of a profile from an http(s) URL or a file path. Raises ValueError with a plain reason."""
    try:
        if source.startswith(("https://", "http://")):
            with urllib.request.urlopen(source, timeout=10) as response:
                raw = response.read(PROFILE_MAX_BYTES + 1)
        else:
            raw = Path(source).expanduser().read_bytes()
    except OSError as e:
        raise ValueError(f"couldn't read it ({getattr(e, 'reason', None) or e.strerror or e})") from None
    if len(raw) > PROFILE_MAX_BYTES:
        raise ValueError("it's too big for a profile (over 64 KB)")
    return raw.decode("utf-8", errors="replace")


def network_env(distro, domain_id, rmw, localhost):
    """Environment variables for the network settings; defaults are left unset."""
    env = {}
    if domain_id:
        env["ROS_DOMAIN_ID"] = str(domain_id)
    if rmw != "default":
        env["RMW_IMPLEMENTATION"] = rmw
    if localhost:  # Humble predates ROS_AUTOMATIC_DISCOVERY_RANGE
        env.update({"ROS_LOCALHOST_ONLY": "1"} if distro == "humble" else {"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"})
    return env


def read_env():
    """{VAR: value} saved by a previous run, so the settings page starts from them."""
    return dict(re.findall(r"^export (\w+)=(\S*)$", ENV_FILE.read_text(), re.M)) if ENV_FILE.exists() else {}


def write_env(env):
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("# Network settings from ROS 2 Installer. Run the installer again to change them.\n"
                        + "".join(f"export {k}={v}\n" for k, v in env.items()))


def parse_lspci(text):
    """Graphics devices in `lspci -mm` output, as short names like "NVIDIA GeForce RTX 4060"."""
    vendors = {"NVIDIA Corporation": "NVIDIA", "Intel Corporation": "Intel",
               "Advanced Micro Devices, Inc. [AMD/ATI]": "AMD"}
    names = []
    for line in text.splitlines():
        f = shlex.split(line)
        if len(f) >= 4 and f[1] in ("VGA compatible controller", "3D controller", "Display controller"):
            model = re.search(r"\[(.+)\]", f[3])  # the marketing name, when lspci knows it
            names.append(f"{vendors.get(f[2], f[2])} {model[1] if model else f[3]}")
    return names


def graphics():
    """("ok"|"warn", description) of the graphics hardware, for the first page."""
    gpus = parse_lspci(run(["lspci", "-mm"]))
    nvidia = Path("/proc/driver/nvidia/version")
    driver = re.search(r"Module\s+([\d.]+)", nvidia.read_text()) if nvidia.exists() else None
    gpus = [f"{g} (driver {driver[1]})" if driver and g.startswith("NVIDIA") else g for g in gpus]
    board = Path("/proc/device-tree/model")  # ARM boards: Jetson, Raspberry Pi
    if gpus:
        return "ok", ", ".join(gpus)
    if board.exists():
        return "ok", board.read_text().strip("\0\n ") + " (built-in graphics)"
    return "warn", "No graphics card found, so Gazebo and RViz will draw with the processor and run slowly."


def computer():
    """("ok"|"warn", processor, cores and memory), for the first page."""
    model = re.search(r"^Model name:\s*(.+)$", run(["lscpu"]), re.M)
    ram = int(re.search(r"^MemTotal:\s*(\d+)", Path("/proc/meminfo").read_text(), re.M)[1]) / 1e6  # GB, as sold
    name = re.sub(r"\((R|TM)\)", "", model[1]).strip() if model else "Unknown processor"
    text = f"{name} · {os.cpu_count()} cores · {ram:.0f} GB memory"
    if ram < MIN_RAM_GB:
        return "warn", f"{text}. Simulators need at least {MIN_RAM_GB} GB to run well."
    return "ok", text


# Programs that show the admin password prompt for pkexec. GNOME, Cinnamon and Budgie have one built in.
# shortcut: a fixed list of known agents; an unlisted one only causes a warning, never blocks installing.
POLKIT_AGENTS = {
    "gnome-shell", "cinnamon", "budgie-polkit-dialog",
    "polkit-gnome-authentication-agent-1", "polkit-kde-authentication-agent-1",
    "polkit-mate-authentication-agent-1", "lxqt-policykit-agent", "lxpolkit", "xfce-polkit",
    "hyprpolkitagent", "soteria",
}


def running_programs():
    names = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0]
        except OSError:  # the process ended while we looked
            continue
        names.add(os.path.basename(argv0.decode(errors="replace")))
    return names


# (program, desktop it comes with, how it takes a command): a list of flags followed by
# the command's own arguments, or "string" for terminals that take the command as one string.
TERMINALS = [
    ("gnome-terminal", "GNOME", ["--"]),
    ("ptyxis", "GNOME", ["--"]),
    ("konsole", "KDE", ["-e"]),
    ("xfce4-terminal", "XFCE", ["-x"]),
    ("mate-terminal", "MATE", ["-x"]),
    ("qterminal", "LXQT", "string"),
    ("lxterminal", "LXDE", "string"),
]


def terminal_command(argv, desktop=None, which=shutil.which):
    """Command that opens a terminal window running argv, preferring the desktop's own terminal."""
    desktop = (desktop if desktop is not None else os.environ.get("XDG_CURRENT_DESKTOP", "")).upper()
    ranked = sorted(TERMINALS, key=lambda t: t[1] not in desktop.split(":"))  # stable: desktop's own first
    for program, _desktop, style in ranked:
        if which(program):
            return [program, "-e", shlex.join(argv)] if style == "string" else [program, *style, *argv]
    return ["x-terminal-emulator", "-e", *argv]  # Debian's alternative; most terminals accept this form


def demo_script(distro, title, command):
    """bash script a Try it button runs: load ROS 2, say how to stop, run the demo, keep the terminal open."""
    start = shlex.quote(f"Starting {title}. Press Ctrl+C in this window to stop it.")
    done = shlex.quote("Stopped. You can type ros2 commands here, or close this window.")
    return (f"source /opt/ros/{distro}/setup.bash; {ENV_SOURCE_LINE}; echo {start}; echo; "
            f"{command}; echo; echo {done}; exec bash")


def terminal_script(distro):
    """bash script for the Open terminal button: a terminal with ROS 2 (and network settings) loaded."""
    hello = shlex.quote("ROS 2 is ready in this terminal. Try: ros2 run turtlesim turtlesim_node")
    return f"source /opt/ros/{distro}/setup.bash; {ENV_SOURCE_LINE}; echo {hello}; echo; exec bash"


def tutorials_url(distro):
    """The official "First steps with ROS" learning path; the same page exists for every distro."""
    return f"https://docs.ros.org/en/{distro}/First-Steps.html"


def a11y_label(widget, text):
    """The name a screen reader announces for a widget that shows no text of its own."""
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return widget


def decorative(icon_name):
    """An icon that only decorates its row's text, so screen readers skip it."""
    return Gtk.Image(icon_name=icon_name, accessible_role=Gtk.AccessibleRole.PRESENTATION)


def status_icon(state):
    icon, name = {"ok": ("emblem-ok-symbolic", "OK"), "warn": ("dialog-warning-symbolic", "Warning"),
                  "error": ("dialog-error-symbolic", "Problem")}[state]
    return a11y_label(Gtk.Image(icon_name=icon), name)


class Installer(Adw.ApplicationWindow):
    def __init__(self, app, parent=None):
        """The installer; with parent, the Create profile editor in its own window on top of it."""
        super().__init__(application=app, title="Create a profile" if parent else "ROS 2 Installer",
                         default_width=760, default_height=720 if parent else 680,
                         transient_for=parent, modal=bool(parent))
        self.parent = parent
        self.check_rows = {}
        self.distro = detect_distro()
        self.blockers = set()
        self.installing = False
        self.update_only = False
        self.connect("close-request", self.on_close_request)
        self.total = 0

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.back = Gtk.Button(label="Back")
        self.next = Gtk.Button(label="Next", css_classes=["suggested-action"])
        self.back.connect("clicked", lambda _b: self.on_back())
        self.next.connect("clicked", lambda _b: self.on_next())
        header = Adw.HeaderBar()
        header.pack_start(self.back)
        menu = Gio.Menu()
        menu.append("Create profile…", "win.create-profile")
        menu.append("Open profile…", "win.load-profile")
        menu.append("Save choices as profile…", "win.save-profile")
        if not parent:
            menu.append("Update ROS 2 packages…", "win.updates")
            menu.append("About ROS 2 Installer", "win.about")
            header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        header.pack_end(self.next)
        self.editing = False  # True in Create profile mode: choices go into a file, nothing is installed
        self.saved_profile = None  # the last profile saved from the editor, for "Use it here"
        for name, handler in [("about", self.show_about), ("load-profile", self.load_profile_dialog),
                              ("save-profile", self.save_profile_dialog), ("create-profile", self.create_profile),
                              ("use-saved", lambda: self.use_profile(self.saved_profile)),
                              ("updates", self.show_updates_dialog)]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, handler=handler: handler())
            action.set_enabled(name == "about" or bool(self.distro))  # profiles need the choose page
            self.add_action(action)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        self.step_bar = self.build_step_bar()
        box.append(self.step_bar)
        box.append(Gtk.Separator())
        box.append(self.stack)
        self.toasts = Adw.ToastOverlay(child=box)
        self.set_content(self.toasts)

        if parent:  # the profile editor only shows the choices
            self.build_choose_page(editing=True)
            self.show_page("choose")
            return
        self.build_check_page()
        if self.distro:
            self.build_choose_page()
        self.show_page("check")
        if PROFILE_SOURCE:
            self.open_profile(PROFILE_SOURCE)

    def open_profile(self, source, forwarded=False):
        """Load a --profile URL or file in the background. forwarded: from a second launch while this
        window is open, so there's no check-page row for it; fill in the choices, or say why not."""
        def load():
            try:
                result = load_profile(fetch_profile(source))
            except ValueError as e:
                result = str(e)
            if forwarded and isinstance(result, str):
                GLib.idle_add(self.toast, f"Couldn't open the profile: {result}")
            else:
                GLib.idle_add(self.use_profile if forwarded else self.apply_profile, result)
        threading.Thread(target=load, daemon=True).start()

    def on_close_request(self, _window):
        """Closing mid-install would cut apt off (its output pipe closes) and can leave dpkg half done."""
        if self.installing:
            self.status.set_label("Please wait until the install finishes. Closing now could leave apt half done.")
        return self.installing  # True keeps the window open

    def show_about(self):
        credits = dict(copyright="© 2026 Lentin Joseph, Runtime Robotics", website="https://runtimerobotics.com",
                       version=__version__, license_type=Gtk.License.APACHE_2_0)
        if hasattr(Adw, "AboutDialog"):  # libadwaita 1.5+ (Ubuntu 24.04 and later)
            Adw.AboutDialog(application_name="ROS 2 Installer", application_icon=APP_ID,
                            developer_name="Lentin Joseph", developers=["Lentin Joseph https://runtimerobotics.com"],
                            comments="A beginner-friendly installer for ROS 2 on Ubuntu.",
                            issue_url=f"{REPO_URL}/issues", **credits).present(self)
        else:  # Ubuntu 22.04's libadwaita 1.1 has no About dialog
            Gtk.AboutDialog(transient_for=self, modal=True, program_name="ROS 2 Installer",
                            logo_icon_name=APP_ID, authors=["Lentin Joseph"],
                            comments="A beginner-friendly installer for ROS 2 on Ubuntu.\nRuntime Robotics",
                            **credits).present()

    # ---- navigation ----

    def replace_page(self, name, widget):
        old = self.stack.get_child_by_name(name)
        if old:
            self.stack.remove(old)
        self.stack.add_named(widget, name)

    STEP_NAMES = ["Check", "Choose", "Review", "Install", "Try it"]
    STEPS = {"check": 0, "choose": 1, "packages": 1, "review": 2, "install": 3, "done": 4}  # page -> step

    def build_step_bar(self):
        """Check ── Choose ── Review ── Install ── Try it: where you are, and what comes next."""
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=8)
        self.step_labels = []
        for i in range(len(self.STEP_NAMES)):
            if i:
                bar.append(Gtk.Label(label="──", css_classes=["dim-label"]))
            self.step_labels.append(Gtk.Label())
            bar.append(self.step_labels[-1])
        return bar

    def show_page(self, name):
        self.stack.set_visible_child_name(name)
        current = self.STEPS[name]
        for i, (label, step) in enumerate(zip(self.step_labels, self.STEP_NAMES)):
            label.set_label(f"{'✓' if i < current else '●' if i == current else '○'} {step}")
            a11y_label(label, f"{step}, {'done' if i < current else 'current step' if i == current else 'still to come'}")
            label.set_css_classes(["accent", "heading"] if i == current else [] if i < current else ["dim-label"])
        self.step_bar.set_visible(not self.editing)  # editing a profile isn't an install
        self.back.set_visible(name in ("choose", "review", "packages"))
        self.back.set_label("Cancel" if self.editing and name == "choose" else "Back")
        self.next.set_visible(name != "install")
        self.next.set_label("Save…" if self.editing and name == "choose" else
                            {"review": "Install", "done": "Close", "packages": "Done"}.get(name, "Next"))
        self.next.set_sensitive(not (name == "check" and self.blockers))

    def on_back(self):
        page = self.stack.get_visible_child_name()
        if self.editing and page == "choose":
            return self.close()  # Cancel: the editor window goes away, nothing saved
        install_back = "choose" if self.update_only else "review"  # an update has no review page
        self.show_page({"choose": "check", "review": "choose", "install": install_back, "packages": "choose"}[page])

    def on_next(self):
        page = self.stack.get_visible_child_name()
        if page in ("check", "packages"):
            self.show_page("choose")
        elif page == "choose" and self.editing:
            self.save_profile_dialog()
        elif page == "choose":
            self.build_review_page()
        elif page == "review":
            self.start_install()
        elif page == "done":
            self.close()

    # ---- 1. system check ----

    def build_check_page(self):
        page = Adw.PreferencesPage()
        page.add(self.build_self_update_group())
        group = Adw.PreferencesGroup(title="Checking your computer",
                                     description="ROS 2 needs a supported Ubuntu version, free disk space and internet.")
        page.add(group)
        self.check_rows = {}
        rows = [("os", "Ubuntu version"), ("arch", "Processor type"), ("computer", "Computer"),
                ("gpu", "Graphics"), ("disk", "Free disk space"), ("net", "Internet connection"),
                ("agent", "Password prompt"), ("ros", "ROS 2 already on this computer")]
        if PROFILE_SOURCE:
            rows.append(("profile", "Workshop profile"))
        for key, title in rows:
            row = Adw.ActionRow(title=title, subtitle="Checking…")
            group.add(row)
            self.check_rows[key] = [row, None]
        self.net_retry = Gtk.Button(label="Check again", valign=Gtk.Align.CENTER, visible=False)
        self.net_retry.connect("clicked", lambda _b: self.run_net_check())
        self.check_rows["net"][0].add_suffix(self.net_retry)
        self.replace_page("check", page)

        osr = os_release()
        name = osr.get("PRETTY_NAME", "Unknown system")
        if self.distro:
            self.set_check("os", "ok", f"{name} → ROS 2 {self.distro.title()}, the ROS 2 version made for it")
        else:
            self.set_check("os", "error", f"{name} is not supported. Use Ubuntu 22.04, 24.04 or 26.04.")

        if ARCH in SUPPORTED_ARCHS:
            self.set_check("arch", "ok", f"{ARCH}: ROS 2 has packages for it")
        else:
            self.set_check("arch", "error", f"{ARCH}: ROS 2 has no packages for it. "
                                            "Use a 64-bit Ubuntu (amd64 or arm64).")
        self.set_check("computer", *computer())
        self.set_check("gpu", *graphics())

        free_gb = shutil.disk_usage("/").free / 1e9
        if free_gb >= MIN_FREE_GB:
            self.set_check("disk", "ok", f"{free_gb:.0f} GB free")
        else:
            self.set_check("disk", "error", f"Only {free_gb:.1f} GB free. Free up at least {MIN_FREE_GB} GB, then reopen the installer.")

        if POLKIT_AGENTS & running_programs():
            self.set_check("agent", "ok", "Your desktop can ask for your password when installing.")
        else:
            self.set_check("agent", "warn",
                           "No password helper found, so installing may fail. On a desktop without one, run "
                           "“sudo apt install policykit-1-gnome”, then log out and back in.")

        found = sorted(os.listdir("/opt/ros")) if os.path.isdir("/opt/ros") else []
        others = [d.title() for d in found if d != self.distro]
        if others:
            self.set_check("ros", "warn", f"Found ROS 2 {', '.join(others)}. Use only one ROS 2 version per terminal.")
        elif found:
            self.set_check("ros", "ok", f"ROS 2 {self.distro.title()} is installed. You can add more parts.")
        else:
            self.set_check("ros", "ok", "None yet. This is a fresh install.")

        self.run_net_check()

    def run_net_check(self):
        def check_net():
            try:
                socket.create_connection(("packages.ros.org", 80), timeout=5).close()
                result = ("ok", "packages.ros.org is reachable")
            except OSError:
                result = ("error", "Can't reach packages.ros.org. Connect to the internet, then press Check again.")
            GLib.idle_add(self.set_check, "net", *result)

        self.blockers.add("net")  # until the check finishes
        self.next.set_sensitive(False)
        self.net_retry.set_visible(False)
        self.check_rows["net"][0].set_subtitle("Checking…")
        threading.Thread(target=check_net, daemon=True).start()

    def build_self_update_group(self):
        """Hidden until a newer release of this installer turns up (checked in the background)."""
        group = Adw.PreferencesGroup(visible=False)
        row = Adw.ActionRow()
        row.add_prefix(decorative("software-update-available-symbolic"))
        row.add_suffix(self.button("Release page", lambda: Gio.AppInfo.launch_default_for_uri(
            f"{REPO_URL}/releases/latest", None)))
        group.add(row)

        def show(version):
            how = ("Download the new .deb from the release page." if str(HERE).startswith("/usr/lib/")
                   else "To update, run the one-line install command again (it's on the release page).")
            row.set_title(f"ROS 2 Installer {version} is available")
            row.set_subtitle(f"You have {__version__}. {how}")
            group.set_visible(True)

        def check():
            version = newer_release(__version__, latest_release_url())
            if version:
                GLib.idle_add(show, version)
        threading.Thread(target=check, daemon=True).start()
        return group

    def set_check(self, key, state, subtitle):
        row, icon = self.check_rows[key]
        if icon:
            row.remove(icon)
        self.check_rows[key][1] = status_icon(state)
        row.add_suffix(self.check_rows[key][1])
        row.set_subtitle(subtitle)
        (self.blockers.add if state == "error" else self.blockers.discard)(key)
        if key == "net":
            self.net_retry.set_visible(state == "error")
        if self.stack.get_visible_child_name() == "check":
            self.next.set_sensitive(not self.blockers)

    # ---- 2. choose ----

    def build_choose_page(self, editing=False):
        d = self.distro
        self.editing = editing
        stale = self.stack.get_child_by_name("packages")  # "Install more": rebuild with fresh Installed marks
        if stale:
            self.stack.remove(stale)
        variants = CATALOG["base"]["variants"]
        goals = CATALOG["goals"]
        everything = [p for v in variants.values() for p in expand(v["packages"], d)] + \
                     [p for g in goals for p in expand(g["packages"], d)]
        have = installed(everything)
        # Before the repo is added apt knows no ROS packages, so assume everything exists.
        avail = parse_policy(run(["apt-cache", "policy", *everything])) if repo_configured() else None

        page = Adw.PreferencesPage()
        self.profile_group = Adw.PreferencesGroup(visible=False)  # filled in by apply_profile
        page.add(self.profile_group)
        if editing:
            page.add(self.build_editor_group())
        base = Adw.PreferencesGroup(title="ROS 2 itself",
                                    description="Pick what the class's computers should install." if editing else
                                    "New to ROS 2? Keep Desktop-Full, tick Learn ROS 2 below, then press Next. "
                                    "You can add more later.")
        page.add(base)
        self.variant_checks = {}
        first = None
        for vid, v in variants.items():
            check = Gtk.CheckButton(group=first, active=v.get("default", False))
            first = first or check
            row = Adw.ActionRow(title=v["title"] + (" (recommended)" if v.get("default") else ""),
                                subtitle=v["description"])
            row.add_prefix(check)
            row.set_activatable_widget(check)
            if set(expand(v["packages"], d)) <= have:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label"]))
            base.add(row)
            self.variant_checks[vid] = check

        group = Adw.PreferencesGroup(title="What do you want to do?",
                                     description="Tick what you want. Press ⓘ to see exactly what each one installs.")
        page.add(group)
        self.have, self.avail, self.pkg_info = have, avail, {}
        threading.Thread(target=self.load_descriptions, args=(everything,), daemon=True).start()
        self.usable = usable_goals(d, avail)
        self.cat_members, self.cat_checks, self.cat_complete = {}, {}, set()
        for c in CATALOG["categories"]:
            members = [g for g in goals if g["category"] == c["id"]]
            if not members:
                continue
            usable = [g for g in members if g["id"] in self.usable]
            missing = [g["title"] for g in members if g["id"] not in self.usable]
            subtitle = ", ".join(g["title"] for g in members)
            if missing:
                subtitle += f" · not available for ROS 2 {d.title()} yet: {', '.join(missing)}"
            row = Adw.ActionRow(title=c["title"], subtitle=subtitle)
            row.add_prefix(decorative(members[0].get("icon", "package-x-generic-symbolic")))
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            complete = usable and {p for g in usable for p in expand(g["packages"], d)} <= have
            if complete:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label"]))
            check.set_sensitive(bool(usable) and (editing or not complete))  # installed: tick only in a profile
            if complete:
                self.cat_complete.add(c["id"])
            row.add_suffix(self.info_button(members, c["title"]))
            row.add_suffix(check)
            row.set_activatable_widget(check)
            group.add(row)
            self.cat_members[c["id"]], self.cat_checks[c["id"]] = members, check
        for cat, check in self.cat_checks.items():
            check.connect("toggled", self.on_category_toggled, cat)

        terminal = Adw.PreferencesGroup(title="Terminal")
        page.add(terminal)
        self.bashrc = Path.home() / ".bashrc"
        self.source_line = f"source /opt/ros/{d}/setup.bash"
        already = self.bashrc.exists() and self.source_line in self.bashrc.read_text()
        self.bashrc_check = Gtk.CheckButton(active=True, sensitive=editing or not already)
        row = Adw.ActionRow(title="Load ROS 2 in every new terminal",
                            subtitle="Already set up" if already else f"Adds “{self.source_line}” to ~/.bashrc")
        row.add_prefix(self.bashrc_check)
        row.set_activatable_widget(self.bashrc_check)
        terminal.add(row)

        page.add(self.build_network_group(read_env()))

        self.manual = set()  # individually picked packages
        extra = Adw.PreferencesGroup(title="Individual packages",
                                     description="For when you know exactly which ROS 2 package you need.")
        self.manual_row = Adw.ActionRow(title="Pick individual packages", subtitle="Search every ROS 2 package",
                                        activatable=True)
        self.manual_row.add_suffix(decorative("go-next-symbolic"))
        self.manual_row.connect("activated", lambda _r: self.open_packages())
        extra.add(self.manual_row)
        page.add(extra)
        self.replace_page("choose", page)

    def build_editor_group(self):
        """Top of the choose page in Create profile mode: name, description and where to start from."""
        group = Adw.PreferencesGroup(
            title="Create a profile",
            description=f"Pick what the class's computers should install, then press Save. Nothing is installed "
                        f"on this computer. The profile is for ROS 2 {self.distro.title()}.")
        self.profile_name = Gtk.Entry(placeholder_text="For example: Nav2 course", width_chars=32, valign=Gtk.Align.CENTER)
        self.profile_desc = Gtk.Entry(placeholder_text="Shown at the top of the students' page", width_chars=32,
                                      valign=Gtk.Align.CENTER)
        for title, entry in [("Name", self.profile_name), ("Description", self.profile_desc)]:
            row = Adw.ActionRow(title=title)
            row.add_suffix(a11y_label(entry, f"Profile {title.lower()}"))
            group.add(row)
        row = Adw.ActionRow(title="Start from")
        for label, start in [("Empty", self.editor_start_empty), ("This computer's setup", self.editor_start_setup),
                             ("File…", lambda: self.pick_file(False, self.editor_open))]:
            row.add_suffix(self.button(label, start))
        group.add(row)
        return group

    def create_profile(self):
        Installer(self.get_application(), parent=self).present()

    def use_profile(self, profile):
        """Fill in a profile's choices to install on this computer (Use it here, Open profile)."""
        if self.installing:
            return self.toast("Wait until the current install finishes.")
        self.apply_profile(profile)
        self.show_page("choose")

    def editor_start_empty(self):
        default = next(k for k, v in CATALOG["base"]["variants"].items() if v.get("default"))
        self.apply_profile({"name": self.profile_name.get_text(), "description": self.profile_desc.get_text(),
                            "distro": None, "ros": default, "categories": [], "packages": [],
                            "network": {"domain_id": 0, "rmw": "default", "localhost": False}, "terminal": True})

    def editor_start_setup(self):
        profile = load_profile(self.profile_from_choices("This computer", setup=True))
        profile["name"] = self.profile_name.get_text() or profile["name"]
        self.apply_profile(profile)

    def editor_open(self, path):
        try:
            self.apply_profile(load_profile(fetch_profile(path)))
        except ValueError as e:
            self.toast(f"Couldn't open the profile: {e}")

    def apply_profile(self, profile):
        """Fill in the choose page from a workshop profile (a dict), or report why it couldn't load (a str).
        Students still review and approve with their password; nothing installs by itself."""
        if isinstance(profile, str):
            self.set_check("profile", "warn", f"Couldn't use the workshop profile: {profile}. "
                                              "You can still choose by hand.")
            return
        if "profile" in self.check_rows and not self.editing:
            self.set_check("profile", "ok", f"{profile['name']}: your choices are filled in on the next page")
        if not self.distro:
            return
        d, notes = self.distro, []
        if profile["distro"] and profile["distro"] != d:
            notes.append(f"it was made for ROS 2 {profile['distro'].title()}, this computer has {d.title()}")
        self.variant_checks[profile["ros"]].set_active(True)
        for check in self.cat_checks.values():  # a profile replaces the current choices
            check.set_active(False)
        self.manual.clear()
        self.manual_row.set_subtitle("Search every ROS 2 package")
        titles = {c["id"]: c["title"] for c in CATALOG["categories"]}
        already = []
        for cat in profile["categories"]:
            check = self.cat_checks[cat]
            if check.get_sensitive():
                check.set_active(True)
            elif cat in self.cat_complete and not self.editing:
                already.append(titles[cat])
            else:
                notes.append(f"{titles[cat]} isn't available for ROS 2 {d.title()}")
        wanted = [p.format(distro=d) for p in profile["packages"]]
        mine = [p for p in wanted if p.startswith(f"ros-{d}-")]
        notes += [f"{p} is for another ROS 2 version" for p in wanted if p not in mine]
        if mine and repo_configured():
            avail = parse_policy(run(["apt-cache", "policy", *mine]))
            notes += [f"{p} isn't available" for p in mine if p not in avail]
            mine = [p for p in mine if p in avail]
        have = installed(mine) if mine and not self.editing else set()  # a profile keeps installed ones
        if have:
            already.append(f"{len(have)} of its package{'s' * (len(have) > 1)}")
        self.manual |= set(mine) - have
        if self.manual:
            self.manual_row.set_subtitle(f"{len(self.manual)} picked")
        net = profile["network"]
        if net:
            self.domain.set_value(net.get("domain_id", 0))
            self.rmw.set_selected([o["id"] for o in self.rmw_options].index(net.get("rmw", "default")))
            self.localhost.set_active(net.get("localhost", False))
            self.advanced.set_expanded(True)  # so students can see what the profile set
        if self.bashrc_check.get_sensitive():
            self.bashrc_check.set_active(profile["terminal"])
        if self.editing:
            self.profile_name.set_text(profile["name"])
            self.profile_desc.set_text(profile["description"])
            if notes:
                self.toast("Not applied: " + "; ".join(notes) + ".")
            return
        to_do = self.manual or any(c.get_active() for c in self.cat_checks.values())
        description = profile["description"]
        if description and not description.endswith((".", "!", "?")):
            description += "."
        text = " ".join(filter(None, [description, "Your choices are filled in; press Next to review."
                                      if to_do else "Everything in this profile is already on this computer."]))
        if already and to_do:
            text += " Already on this computer: " + ", ".join(already) + "."
        if notes:
            text += " Not applied: " + "; ".join(notes) + "."
        row = Adw.ActionRow(title=GLib.markup_escape_text(f"Workshop profile: {profile['name']}"),
                            subtitle=GLib.markup_escape_text(text))
        row.add_prefix(decorative("emblem-documents-symbolic"))
        if getattr(self, "profile_row", None) and self.profile_row.get_parent():
            self.profile_group.remove(self.profile_row)  # loading another profile replaces the banner
        self.profile_row = row
        self.profile_group.add(row)
        self.profile_group.set_visible(True)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def pick_file(self, save, callback, initial="my-course.yaml"):
        """Ask for a .yaml file to open or save; calls callback(path) unless the user cancels."""
        yaml_files = Gtk.FileFilter(name="Workshop profiles (*.yaml)")
        yaml_files.add_pattern("*.yaml")
        yaml_files.add_pattern("*.yml")
        title = "Save profile" if save else "Open profile"
        if hasattr(Gtk, "FileDialog"):  # GTK 4.10+: Ubuntu 24.04 and later
            dialog = Gtk.FileDialog(title=title, default_filter=yaml_files)
            if save:
                dialog.set_initial_name(initial)

            def done(d, result):
                try:
                    chosen = (d.save_finish if save else d.open_finish)(result)
                except GLib.Error:  # cancelled
                    return
                if chosen:
                    callback(chosen.get_path())
            (dialog.save if save else dialog.open)(self, None, done)
        else:  # Ubuntu 22.04's GTK 4.6
            chooser = Gtk.FileChooserNative(title=title, transient_for=self, modal=True,
                                            action=Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN)
            chooser.add_filter(yaml_files)
            if save:
                chooser.set_current_name(initial)

            def response(c, answer):
                if answer == Gtk.ResponseType.ACCEPT:
                    callback(c.get_file().get_path())
            chooser.connect("response", response)
            self.chooser = chooser  # keep it alive while it's open
            chooser.show()

    def load_profile_dialog(self):
        def load(path):
            try:
                profile = load_profile(fetch_profile(path))
            except ValueError as e:
                self.toast(f"Couldn't open the profile: {e}")
                return
            self.use_profile(profile)
            self.toast(f"Loaded “{profile['name']}”. Check the choices, then press Next.")
        self.pick_file(False, load)

    def profile_from_choices(self, name, setup=False, description=""):
        """Profile YAML from what was picked by hand, or (setup=True) from what's installed on this computer,
        so one set-up computer can be copied to a whole class. Packages use {distro}: the file works on any version."""
        d, variants = self.distro, CATALOG["base"]["variants"]
        picked = next(k for k, c in self.variant_checks.items() if c.get_active())
        if setup:
            on_pc = [vid for vid, v in variants.items() if set(expand(v["packages"], d)) <= self.have]
            ros = on_pc[0] if on_pc else picked  # catalog order: biggest first
            categories = [cat for cat in self.cat_checks if cat in self.cat_complete]
            covered = {p for v in variants.values() for p in expand(v["packages"], d)} | \
                      {p for g in CATALOG["goals"] for p in expand(g["packages"], d)}
            deps = set(run(["apt-cache", "depends", "--recurse", "--no-recommends", "--no-suggests", "--no-conflicts",
                            "--no-breaks", "--no-replaces", "--no-enhances", *expand(variants[ros]["packages"], d)]).split())
            chosen = extra_packages(d, run(["apt-mark", "showmanual"]).split(), covered, deps)
        else:
            ros = picked
            categories = [cat for cat, check in self.cat_checks.items() if check.get_active()]
            chosen = self.manual
        packages = sorted(p.replace(f"ros-{d}-", "ros-{distro}-", 1) for p in chosen)
        rmw = self.rmw_options[self.rmw.get_selected()]["id"]
        network = {k: v for k, v in [("domain_id", self.domain.get_value_as_int()), ("rmw", rmw),
                                      ("localhost", self.localhost.get_active())]
                   if v not in (0, "default", False)}
        return profile_text(name, ros, categories, packages, network, self.bashrc_check.get_active(), description, d)

    def save_profile_dialog(self):
        """Save the current choices. In Create profile mode the name and description come from the editor."""
        name = self.profile_name.get_text().strip() if self.editing else ""
        if self.editing and not name:
            self.toast("Give the profile a name first.")
            self.profile_name.grab_focus()
            return
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "my-course"

        def save(path):
            path = Path(path) if Path(path).suffix in (".yaml", ".yml") else Path(path + ".yaml")
            title = name or path.stem.replace("-", " ").replace("_", " ").strip().capitalize() or "My course"
            text = self.profile_from_choices(title, description=self.profile_desc.get_text().strip()
                                             if self.editing else "")
            try:
                path.write_text(text)
            except OSError as e:
                self.toast(f"Couldn't save the profile: {e.strerror}")
                return
            if self.editing:  # hand the profile to the installer window, and close the editor
                self.parent.saved_profile = load_profile(text)
                self.parent.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"Saved “{title}” to {path}"),
                                                       button_label="Use it here", action_name="win.use-saved",
                                                       timeout=10))
                self.close()
            else:
                self.toast(f"Saved “{title}” to {path}")
        self.pick_file(True, save, f"{slug}.yaml")

    def on_category_toggled(self, check, cat):
        """Ticking a category also ticks the categories its sections need (Nav2 needs Simulate)."""
        if check.get_active():
            cat_of = {g["id"]: g["category"] for g in CATALOG["goals"]}
            for g in self.cat_members[cat]:
                for req in g.get("requires", []):
                    other = self.cat_checks[cat_of[req]]
                    if other.get_sensitive():
                        other.set_active(True)

    def load_descriptions(self, pkgs):
        self.pkg_info = parse_show(run(["apt-cache", "show", "--no-all-versions", *pkgs]))

    def info_button(self, goals, name):
        """ⓘ button whose popover lists what the given sections install."""
        button = Gtk.MenuButton(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=f"What's included in {name}", css_classes=["flat"])
        a11y_label(button, f"What's included in {name}")
        button.set_create_popup_func(lambda b: b.set_popover(self.info_popover(goals)))  # built when opened
        return button

    def info_popover(self, goals):
        d = self.distro
        titles = {g["id"]: g["title"] for g in CATALOG["goals"]}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)

        def label(text, *classes, **kw):
            box.append(Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=60,
                                 css_classes=list(classes), **kw))

        for i, g in enumerate(goals):
            if len(goals) > 1:
                label(g["title"], "heading", margin_top=8 * (i > 0))
            label(g["description"], "dim-label")
            if g.get("requires"):
                label("Also installs: " + ", ".join(titles[r] for r in g["requires"]))
            for pkg in expand(g["packages"], d):
                if pkg in self.have:
                    state = "✓ installed"
                elif self.avail is not None and pkg not in self.avail:
                    state = f"✗ not released for {d.title()}"
                else:
                    state = "will be installed"
                label(f"{pkg}  ({state})", "monospace", margin_top=4)
                if pkg in self.pkg_info:
                    label(self.pkg_info[pkg], "dim-label", "caption", margin_start=12)
            if "demo" in g and for_distro(g["demo"]["command"], d):
                label(f"Try it: {for_distro(g['demo']['command'], d)}", "caption", margin_top=6)
        scroller = Gtk.ScrolledWindow(child=box, max_content_height=460, propagate_natural_height=True,
                                      propagate_natural_width=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        return Gtk.Popover(child=scroller)

    def show_updates_dialog(self):
        """☰ → Update ROS 2 packages…: what's waiting, Update now, and the daily notification switch."""
        if self.installing:
            return self.toast("Wait until the current install finishes.")
        dialog = Adw.Window(transient_for=self, modal=True, title="Update ROS 2", default_width=560, default_height=520)
        header = Adw.HeaderBar()
        update = Gtk.Button(label="Update now", css_classes=["suggested-action"], sensitive=False)
        header.pack_end(update)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        summary = Adw.ActionRow(title="Checking for ROS 2 updates…")
        listing = Adw.ExpanderRow(title="Packages", visible=False)
        group.add(summary)
        group.add(listing)
        page.add(group)

        notify = Adw.PreferencesGroup()
        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=update_notifications_on())
        row = Adw.ActionRow(title="Notify me when ROS 2 updates are ready",
                            subtitle="Checks once a day and shows a desktop notification.")
        row.add_suffix(switch)
        row.set_activatable_widget(switch)

        def on_switch(sw, _param):
            if not set_update_notifications(sw.get_active()):
                row.set_subtitle("Couldn't set up the daily check (systemd user session not available).")
        switch.connect("notify::active", on_switch)
        notify.add(row)
        page.add(notify)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(page)
        dialog.set_content(box)
        found = []

        def show(pkgs):
            found[:] = pkgs
            n = len(pkgs)
            if not n:
                summary.set_title("ROS 2 is up to date")
                summary.set_subtitle("No updates for your installed ROS 2 packages.")
                return
            summary.set_title(f"{n} ROS 2 package update{'s' * (n > 1)} ready")
            summary.set_subtitle("Update now downloads and installs them. Your computer asks for your password once.")
            for p in pkgs[:200]:  # the expander stays quick; the count above is the full number
                listing.add_row(Adw.ActionRow(title=p))
            listing.set_subtitle(f"{n} packages" + (", first 200 shown" if n > 200 else ""))
            listing.set_visible(True)
            update.set_sensitive(True)

        if not os.path.isdir(f"/opt/ros/{self.distro}"):
            summary.set_title("ROS 2 isn't installed yet")
            summary.set_subtitle("Install it first; updates show up here afterwards.")
        else:
            threading.Thread(target=lambda: GLib.idle_add(show, upgradable(self.distro)), daemon=True).start()
        update.connect("clicked", lambda _b: (dialog.close(), self.start_install(updates=list(found))))
        dialog.present()

    def build_network_group(self, saved):
        group = Adw.PreferencesGroup(title="Network")
        # Folded away: a wrong value quietly stops a computer seeing its robot. Open if set before.
        self.advanced = advanced = Adw.ExpanderRow(title="Advanced: network settings", expanded=bool(saved),
                                   subtitle="Leave these alone unless your teacher or lab says otherwise. "
                                            "Computers and robots only see each other when these match.")
        group.add(advanced)
        self.domain = Gtk.SpinButton.new_with_range(0, 101, 1)
        self.domain.set_valign(Gtk.Align.CENTER)
        self.domain.set_value(int(saved.get("ROS_DOMAIN_ID", 0)))
        row = Adw.ActionRow(title="ROS Domain ID",
                            subtitle="Give each student or robot its own number (0–101) so they don't mix up.")
        row.add_suffix(self.domain)
        advanced.add_row(row)

        self.rmw_options = CATALOG["network"]["rmw"]
        ids = [o["id"] for o in self.rmw_options]
        self.rmw = Adw.ComboRow(title="Middleware (RMW)",
                                model=Gtk.StringList.new([o["title"] for o in self.rmw_options]))
        self.rmw.connect("notify::selected", lambda r, _p: r.set_subtitle(self.rmw_options[r.get_selected()]["description"]))
        saved_rmw = saved.get("RMW_IMPLEMENTATION")
        self.rmw.set_selected(ids.index(saved_rmw) if saved_rmw in ids else 0)
        self.rmw.set_subtitle(self.rmw_options[self.rmw.get_selected()]["description"])
        advanced.add_row(self.rmw)

        self.localhost = Gtk.Switch(valign=Gtk.Align.CENTER,
                                    active="ROS_LOCALHOST_ONLY" in saved or "ROS_AUTOMATIC_DISCOVERY_RANGE" in saved)
        row = Adw.ActionRow(title="This computer only",
                            subtitle="Keep ROS 2 traffic off the network. Good for simulation-only classes.")
        row.add_suffix(self.localhost)
        row.set_activatable_widget(self.localhost)
        advanced.add_row(row)
        return group

    def network_choice(self):
        """(env vars to save, extra package or None) for the network settings shown."""
        option = self.rmw_options[self.rmw.get_selected()]
        env = network_env(self.distro, self.domain.get_value_as_int(), option["id"], self.localhost.get_active())
        return env, option.get("package", "").format(distro=self.distro) or None

    def selection(self):
        """(titles, packages) the user picked, packages de-duplicated in order."""
        vid = next(k for k, c in self.variant_checks.items() if c.get_active())
        variant = CATALOG["base"]["variants"][vid]
        goals = with_required(g["id"] for cat, check in self.cat_checks.items() if check.get_active()
                              for g in self.cat_members[cat] if g["id"] in self.usable)
        titles = [f"ROS 2 {variant['title']}"] + [g["title"] for g in goals]
        pkgs = expand(variant["packages"], self.distro) + [p for g in goals for p in expand(g["packages"], self.distro)]
        if self.manual:
            titles.append(f"{len(self.manual)} individual package{'s' * (len(self.manual) > 1)}")
            pkgs += sorted(self.manual)
        env, rmw_pkg = self.network_choice()
        if env:
            titles.append("Network: " + ", ".join(f"{k}={v}" for k, v in env.items()))
        if rmw_pkg:
            pkgs.append(rmw_pkg)
        return titles, list(dict.fromkeys(pkgs))

    # ---- individual packages ----

    def open_packages(self):
        if not self.stack.get_child_by_name("packages"):
            spinner = Adw.StatusPage(title="Loading the package list…")
            self.replace_page("packages", spinner)

            def load():
                names = parse_search(run(["apt-cache", "search", "--names-only", f"^ros-{self.distro}-"]))
                have = installed([f"ros-{self.distro}-*"])
                GLib.idle_add(self.build_packages_page, names, have)
            threading.Thread(target=load, daemon=True).start()
        self.show_page("packages")

    def build_packages_page(self, descriptions, have):
        if not descriptions:
            self.replace_page("packages", Adw.StatusPage(
                icon_name="dialog-information-symbolic", title="No package list yet",
                description="Install ROS 2 itself first (go back and press Next), then run the installer again."))
            self.show_page("packages")
            return
        self.pkg_desc, self.pkg_installed, self.query = descriptions, have, []
        # Search matches the start of words, so "imu" finds imu-tools but not "simulation".
        self.pkg_words = {n: re.findall(r"[a-z0-9]+", f"{n} {d}".lower()) for n, d in descriptions.items()}
        search = Gtk.SearchEntry(placeholder_text=f"Search {len(descriptions):,} packages, e.g. joy, lidar, moveit")
        self.picked_label = Gtk.Label(xalign=0, css_classes=["dim-label"])
        self.update_picked()
        pkg_filter = Gtk.CustomFilter.new(self.package_matches)

        def on_search(entry):
            self.query = entry.get_text().lower().split()
            pkg_filter.changed(Gtk.FilterChange.DIFFERENT)
        search.connect("search-changed", on_search)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_package_row)
        factory.connect("bind", self.bind_package_row)
        model = Gtk.FilterListModel(model=Gtk.StringList.new(sorted(descriptions)), filter=pkg_filter)
        view = Gtk.ListView(model=Gtk.NoSelection(model=model), factory=factory)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        page.append(search)
        page.append(self.picked_label)
        page.append(Gtk.ScrolledWindow(child=view, vexpand=True))
        self.replace_page("packages", page)
        self.show_page("packages")
        search.grab_focus()

    def package_matches(self, item):
        words = self.pkg_words[item.get_string()]
        return all(any(w.startswith(q) for w in words) for q in self.query)

    def setup_package_row(self, _factory, list_item):
        name = Gtk.Label(xalign=0)
        desc = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "caption"])
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        labels.append(name)
        labels.append(desc)
        check = Gtk.CheckButton(child=labels, margin_top=4, margin_bottom=4)  # clicking the text ticks it too
        check.connect("toggled", self.on_package_toggled)
        row = Gtk.Box(spacing=12)
        row.append(check)
        row.append(Gtk.Label(label="Installed", css_classes=["dim-label"]))
        list_item.set_child(row)

    def bind_package_row(self, _factory, list_item):
        # Rows are recycled while scrolling, so every bind re-labels the widgets for its package.
        row = list_item.get_child()
        check, badge = row.get_first_child(), row.get_last_child()
        pkg = list_item.get_item().get_string()
        labels = check.get_child()
        labels.get_first_child().set_label(pkg)
        labels.get_last_child().set_label(self.pkg_desc[pkg])
        check.pkg = None  # stops on_package_toggled from reacting to the set_active below
        check.set_active(pkg in self.manual or (pkg in self.pkg_installed and not self.editing))
        check.set_sensitive(self.editing or pkg not in self.pkg_installed)  # installed: pick only into a profile
        badge.set_visible(pkg in self.pkg_installed)
        check.pkg = pkg

    def on_package_toggled(self, check):
        if getattr(check, "pkg", None):
            (self.manual.add if check.get_active() else self.manual.discard)(check.pkg)
            self.update_picked()

    def update_picked(self):
        n = len(self.manual)
        text = f"{n} picked: {', '.join(sorted(self.manual))}" if n else "Nothing picked yet"
        self.picked_label.set_label(text)
        self.manual_row.set_subtitle(f"{n} picked" if n else "Search every ROS 2 package")

    # ---- 3. review ----

    def build_review_page(self):
        titles, pkgs = self.selection()
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Ready to install",
                                     description="Press Install. Your computer will ask for your password once.")
        page.add(group)
        group.add(Adw.ActionRow(title="You picked", subtitle=", ".join(titles)))
        self.new_row = Adw.ActionRow(title="Packages to install", subtitle="Working it out…")
        self.size_row = Adw.ActionRow(title="Download size", subtitle="Working it out…")
        group.add(self.new_row)
        group.add(self.size_row)
        listing = Adw.ExpanderRow(title="Package list", subtitle=f"{len(pkgs)} packages (plus what they need)")
        for p in pkgs:
            listing.add_row(Adw.ActionRow(title=p))
        group.add(listing)
        self.replace_page("review", page)
        self.show_page("review")
        self.total = 0

        if not repo_configured():
            for row in (self.new_row, self.size_row):
                row.set_subtitle("Worked out after the ROS 2 repository is added")
            return
        self.next.set_sensitive(False)

        def work():
            result = plan_install(pkgs)
            GLib.idle_add(self.show_plan, result)
        threading.Thread(target=work, daemon=True).start()

    def show_plan(self, result):
        if result is None:
            self.new_row.set_subtitle("Some packages weren't found. The installer will refresh the package lists and try anyway.")
            self.size_row.set_subtitle("Unknown")
        else:
            self.total, size = result
            self.new_row.set_subtitle(f"{self.total} new or updated" if self.total else "Everything is already installed")
            self.size_row.set_subtitle(GLib.format_size(size))
        self.next.set_sensitive(True)

    # ---- 4. install ----

    def start_install(self, updates=None):
        """Install the current choices, or (updates) only update those installed ROS 2 packages."""
        self.update_only = bool(updates)
        if updates:
            pkgs, self.upgrades, self.want_bashrc = [], updates, False
            self.net_env = None  # an update leaves the network settings and .bashrc as they are
        else:
            _titles, pkgs = self.selection()
            self.upgrades = []
            self.want_bashrc = self.bashrc_check.get_active() and self.bashrc_check.get_sensitive()
            self.net_env = self.network_choice()[0]
        self.saw_download = False

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        self.status = Gtk.Label(label="Waiting for your password…", wrap=True, css_classes=["title-3"])
        self.progress = Gtk.ProgressBar(show_text=True, text="")
        self.log = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_end = self.log.get_buffer().create_mark(None, self.log.get_buffer().get_end_iter(), False)
        scroller = Gtk.ScrolledWindow(child=self.log, vexpand=True, min_content_height=240)
        self.details = Gtk.Expander(label="Show details", child=scroller)
        self.retry = Gtk.Button(label="Try again", css_classes=["pill", "suggested-action"])
        self.retry.connect("clicked", lambda _b: self.start_install(updates))
        # Shown after a failure, so a student can send the log instead of describing the problem.
        self.copy_log = Gtk.Button(label="Copy details", css_classes=["pill"])
        self.copy_log.connect("clicked", lambda _b: self.copy_report())
        self.open_log = Gtk.Button(label="Open log", css_classes=["pill"])
        self.open_log.connect("clicked", lambda _b: Gio.AppInfo.launch_default_for_uri(
            self.log_path.as_uri(), None))
        self.fail_buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, visible=False)
        for b in (self.copy_log, self.open_log, self.retry):
            self.fail_buttons.append(b)
        for w in (self.status, self.progress, self.details, self.fail_buttons):
            page.append(w)
        self.replace_page("install", page)
        self.show_page("install")
        self.installing = True
        threading.Thread(target=self.install_worker, args=(pkgs,), daemon=True).start()

    def stream(self, cmd):
        """Run cmd, feeding each output line to the log; return its exit code."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            GLib.idle_add(self.on_line, line)
        return proc.wait()

    def install_worker(self, pkgs):
        upgrades = ["--upgrade", *self.upgrades] if self.upgrades else []
        code = self.stream(["pkexec", str(HELPER), "install", *pkgs, *upgrades])
        if code == 0:
            # User-level setup: rosdep's cache and ~/.bashrc belong to the user, not root.
            GLib.idle_add(self.on_line, "##STEP Updating rosdep's list of what ROS 2 projects need\n")
            if self.stream(["bash", "-c", "command -v rosdep >/dev/null && rosdep update || true"]):
                GLib.idle_add(self.on_line, "rosdep update failed; run it later in a terminal.\n")
            if self.want_bashrc:
                with self.bashrc.open("a") as f:
                    f.write(f"\n# Added by ROS 2 Installer\n{self.source_line}\n")
            if self.net_env is not None:
                write_env(self.net_env)  # always rewritten, so turning a setting off sticks
                bashrc_text = self.bashrc.read_text() if self.bashrc.exists() else ""
                if self.net_env and ENV_SOURCE_LINE not in bashrc_text:
                    with self.bashrc.open("a") as f:
                        f.write(f"\n# ROS 2 network settings (ROS 2 Installer)\n{ENV_SOURCE_LINE}\n")
        GLib.idle_add(self.on_finished, code)

    def on_line(self, line):
        status = parse_status(line)
        if status:
            # Downloading fills the first half of the bar and installing the second half.
            # When everything is already downloaded, installing gets the whole bar.
            kind, pct, desc = status
            if kind == "dl":
                self.saw_download = True
                fraction = pct / 200
            else:
                fraction = 0.5 + pct / 200 if self.saw_download else pct / 100
            self.progress.set_fraction(min(max(fraction, self.progress.get_fraction()), 1))
            self.progress.set_text(f"{desc} ({fraction:.0%})")
            return  # status lines are for the bar, not the log
        buf = self.log.get_buffer()
        buf.insert(buf.get_end_iter(), f"\n▶ {line[7:]}" if line.startswith("##STEP ") else line)
        self.log.scroll_mark_onscreen(self.log_end)
        if line.startswith("Waiting for cache lock"):
            self.status.set_label("Waiting for Ubuntu's own updates to finish. This can take a few minutes…")
        elif line.startswith("##STEP "):
            self.status.set_label(line[7:].strip() + "…")
            if line.startswith(("##STEP Installing packages", "##STEP Updating ROS 2 packages")):
                self.progress.set_fraction(0)  # each apt run (install, then updates) fills the bar again
                self.saw_download = False
        elif self.progress.get_fraction() == 0:
            self.progress.pulse()  # e.g. "apt update", before apt reports real progress

    def report(self):
        buf = self.log.get_buffer()
        return log_report(self.distro, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))

    def copy_report(self):
        text = self.report()
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
        self.copy_log.set_label("Copied")

    def on_finished(self, code):
        self.installing = False
        try:
            self.log_path = save_log(self.report())
        except OSError:  # a full disk can be why the install failed; don't hide that failure behind this one
            self.log_path = None
        if code == 0:
            self.progress.set_fraction(1)
            self.build_done_page()
            return
        if code == 126:  # pkexec: the user closed the password prompt
            self.status.set_label("The password prompt was closed, so nothing was installed.")
        elif code == 127:  # pkexec: not authorized, or no password helper (polkit agent) to ask
            self.status.set_label("Couldn't get admin rights, so nothing was installed. Check that your user "
                                  "is an administrator and that a password helper is running (see the first page).")
        else:
            buf = self.log.get_buffer()
            reason = explain_failure(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
            self.status.set_label(reason or "Something went wrong. Open “Show details” to see why, then try again.")
            self.details.set_expanded(True)
        self.fail_buttons.set_visible(True)
        self.open_log.set_visible(self.log_path is not None)
        self.back.set_visible(True)

    # ---- 5. done / try it ----

    def build_done_page(self):
        d = self.distro
        status = Adw.StatusPage(icon_name="emblem-ok-symbolic", title=f"ROS 2 {d.title()} is ready",
                                description="Your ROS 2 packages are up to date." if self.update_only
                                else "Everything is installed. Here's how to start.")
        nxt = Adw.PreferencesGroup(title="What next?")
        loads = self.bashrc.exists() and self.source_line in self.bashrc.read_text()
        row = Adw.ActionRow(title="1. Open a terminal",
                            subtitle="A terminal is a window where you type commands (or press Ctrl+Alt+T). "
                                     + ("New ones load ROS 2 for you." if loads
                                        else f"In a terminal you open yourself, first type: {self.source_line}"),
                            subtitle_selectable=True)
        row.add_suffix(self.button("Open terminal", lambda: subprocess.Popen(
            terminal_command(["bash", "-c", terminal_script(d)]))))
        nxt.add(row)
        if installed([f"ros-{d}-turtlesim"]):
            title, first = "Turtlesim", "ros2 run turtlesim turtlesim_node"
        else:  # ROS-Base has no turtlesim
            title, first = "Check that ROS 2 works", "ros2 doctor --report"
        row = Adw.ActionRow(title="2. Run your first ROS 2 program", subtitle=f"{first}   (Ctrl+C stops it)",
                            subtitle_selectable=True)
        row.add_suffix(self.button("Run", lambda: self.try_demo(title, first)))
        nxt.add(row)
        row = Adw.ActionRow(title="3. Follow the beginner tutorials", subtitle="The official step-by-step ROS 2 lessons")
        row.add_suffix(self.button("Open", lambda: Gio.AppInfo.launch_default_for_uri(tutorials_url(d), None)))
        nxt.add(row)

        # Every demo is listed; ones whose packages aren't installed are shown but can't be run.
        group = Adw.PreferencesGroup(title="Try a demo",
                                     description="Each one opens a terminal. Press Ctrl+C there to stop it.")
        demos = [(m, expand(m.get("packages", []), d), "Comes with ROS 2 Desktop and Desktop-Full.") for m in CATALOG["base"]["demos"]]
        cat_title = {c["id"]: c["title"] for c in CATALOG["categories"]}
        demos += [(g["demo"], expand(g["packages"], d),
                   f"Press Install more below and tick “{cat_title[g['category']]}”.")
                  for g in CATALOG["goals"] if "demo" in g]
        demos = [(demo, needs, hint, for_distro(demo["command"], d)) for demo, needs, hint in demos]
        have = installed([p for _demo, needs, _hint, _cmd in demos for p in needs])
        rows = [(demo, cmd, set(needs) <= have, hint) for demo, needs, hint, cmd in demos
                if cmd]  # no command: no demo for this distro on this architecture
        for demo, command, ready, hint in sorted(rows, key=lambda r: not r[2]):  # runnable ones first
            row = Adw.ActionRow(title=demo["title"], subtitle=command)
            if not ready:
                row.add_suffix(Gtk.Label(label="Not installed", css_classes=["dim-label"]))
            button = self.button("Run", lambda cmd=command, title=demo["title"]: self.try_demo(title, cmd))
            button.set_sensitive(ready)
            button.set_tooltip_text(None if ready else hint)
            row.add_suffix(button)
            group.add(row)
        more = Gtk.Button(label="Install more", halign=Gtk.Align.CENTER, margin_top=18, css_classes=["pill"])
        more.connect("clicked", lambda _b: (self.build_choose_page(), self.show_page("choose")))  # fresh "Installed" marks
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.append(nxt)
        box.append(group)
        box.append(more)
        status.set_child(Adw.Clamp(child=box))
        self.replace_page("done", status)
        self.show_page("done")

    @staticmethod
    def button(label, on_click):
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: on_click())
        return button

    def try_demo(self, title, command):
        subprocess.Popen(terminal_command(["bash", "-c", demo_script(self.distro, title, command)]))


def main():
    if "--version" in sys.argv:
        print(f"ROS 2 Installer {__version__}")
        return 0
    global PROFILE_SOURCE
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--profile":
            if i + 1 == len(args):
                sys.exit("usage: ros2-gui-installer --profile URL-or-file")
            PROFILE_SOURCE = args[i + 1]
        elif arg.startswith("--profile="):
            PROFILE_SOURCE = arg.split("=", 1)[1]
    if PROFILE_SOURCE and not PROFILE_SOURCE.startswith(("https://", "http://")):
        PROFILE_SOURCE = str(Path(PROFILE_SOURCE).expanduser().resolve())  # an open window has another cwd
    if "--check-updates" in sys.argv:
        check_updates()
        return 0
    if os.geteuid() == 0:
        sys.exit("Run the installer without sudo; it asks for your password when it needs it.")
    GLib.set_prgname(APP_ID)  # X11 window class, so the dock matches the window to its menu entry and icon
    app = Adw.Application(application_id=APP_ID)

    def main_window():
        return next((w for w in app.get_windows() if isinstance(w, Installer) and not w.parent), None)

    def activate(a):
        if main_window():  # launched again while open: bring the window forward, don't open a second one
            return main_window().present()
        # The icon ships in data/, so it shows even when running from a git clone.
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(HERE / "data"))
        Gtk.Window.set_default_icon_name(APP_ID)
        Installer(a).present()
    app.connect("activate", activate)

    def open_profile(_action, source):
        activate(app)
        win = main_window()
        if win.distro:  # without a supported Ubuntu there's no choose page to fill in
            win.open_profile(source.get_string(), forwarded=True)
    action = Gio.SimpleAction.new("open-profile", GLib.VariantType.new("s"))
    action.connect("activate", open_profile)
    app.add_action(action)
    app.register(None)
    if app.get_is_remote() and PROFILE_SOURCE:  # already open: hand the profile to that window
        app.activate_action("open-profile", GLib.Variant("s", PROFILE_SOURCE))
        app.get_dbus_connection().flush_sync(None)  # send it before this process exits
        return 0
    return app.run(sys.argv[:1])  # our options are handled above; GTK has none to parse


if __name__ == "__main__":
    sys.exit(main())
