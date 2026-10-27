#!/usr/bin/env python3
"""ROS 2 GUI installer: system check -> choose -> review -> install -> try it.

Runs as the normal user; only helper.sh runs as root (through pkexec).
Uses GTK4 + libadwaita 1.1 widgets only, so it works on Ubuntu 22.04 too.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import yaml
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

HERE = Path(__file__).resolve().parent
HELPER = HERE / "helper.sh"
CATALOG = yaml.safe_load((HERE / "catalog.yaml").read_text())
REPO_URL = "https://github.com/runtimerobotics/ros2_gui_installer"
MIN_FREE_GB = 10  # Desktop-Full + Gazebo is ~6-8 GB once unpacked
ENV_FILE = Path.home() / ".config" / "ros2-gui-installer" / "env.sh"
ENV_SOURCE_LINE = '[ -f "$HOME/.config/ros2-gui-installer/env.sh" ] && source "$HOME/.config/ros2-gui-installer/env.sh"'


def run(cmd):
    """Run a read-only command and return its stdout ('' if the tool is missing)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def os_release():
    pairs = (line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    return {k: v.strip('"') for k, v in pairs}


def for_distro(value, distro):
    """Catalog values are either shared, or a {default: ..., <distro>: ...} map."""
    return value.get(distro, value["default"]) if isinstance(value, dict) else value


def expand(pkgs, distro):
    return [p.format(distro=distro) for p in for_distro(pkgs, distro)]


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
    distro = CATALOG["distros"].get(os_release().get("VERSION_ID"))
    pkgs = upgradable(distro) if distro else []
    print(f"{len(pkgs)} ROS 2 package updates ready")
    if pkgs and shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=ROS 2 Installer", "--icon=system-software-update",
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


def status_icon(state):
    return Gtk.Image(icon_name={"ok": "emblem-ok-symbolic", "warn": "dialog-warning-symbolic",
                                "error": "dialog-error-symbolic"}[state])


class Installer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="ROS 2 Installer", default_width=760, default_height=680)
        self.distro = CATALOG["distros"].get(os_release().get("VERSION_ID"))
        self.blockers = set()
        self.total = 0

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.back = Gtk.Button(label="Back")
        self.next = Gtk.Button(label="Next", css_classes=["suggested-action"])
        self.back.connect("clicked", lambda _b: self.on_back())
        self.next.connect("clicked", lambda _b: self.on_next())
        header = Adw.HeaderBar()
        header.pack_start(self.back)
        menu = Gio.Menu()
        menu.append("About ROS 2 Installer", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        header.pack_end(self.next)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self.show_about())
        self.add_action(about)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.stack)
        self.set_content(box)

        self.build_check_page()
        if self.distro:
            self.build_choose_page()
        self.show_page("check")

    def show_about(self):
        credits = dict(copyright="© 2026 Lentin Joseph, Runtime Robotics", website="https://runtimerobotics.com")
        if hasattr(Adw, "AboutDialog"):  # libadwaita 1.5+ (Ubuntu 24.04 and later)
            Adw.AboutDialog(application_name="ROS 2 Installer", application_icon="system-software-install",
                            developer_name="Lentin Joseph", developers=["Lentin Joseph https://runtimerobotics.com"],
                            comments="A beginner-friendly installer for ROS 2 on Ubuntu.",
                            issue_url=f"{REPO_URL}/issues", **credits).present(self)
        else:  # Ubuntu 22.04's libadwaita 1.1 has no About dialog
            Gtk.AboutDialog(transient_for=self, modal=True, program_name="ROS 2 Installer",
                            logo_icon_name="system-software-install", authors=["Lentin Joseph"],
                            comments="A beginner-friendly installer for ROS 2 on Ubuntu.\nRuntime Robotics",
                            **credits).present()

    # ---- navigation ----

    def replace_page(self, name, widget):
        old = self.stack.get_child_by_name(name)
        if old:
            self.stack.remove(old)
        self.stack.add_named(widget, name)

    def show_page(self, name):
        self.stack.set_visible_child_name(name)
        self.back.set_visible(name in ("choose", "review", "packages"))
        self.next.set_visible(name != "install")
        self.next.set_label({"review": "Install", "done": "Close", "packages": "Done"}.get(name, "Next"))
        self.next.set_sensitive(not (name == "check" and self.blockers))

    def on_back(self):
        page = self.stack.get_visible_child_name()
        self.show_page({"choose": "check", "review": "choose", "install": "review", "packages": "choose"}[page])

    def on_next(self):
        page = self.stack.get_visible_child_name()
        if page in ("check", "packages"):
            self.show_page("choose")
        elif page == "choose":
            self.build_review_page()
        elif page == "review":
            self.start_install()
        elif page == "done":
            self.close()

    # ---- 1. system check ----

    def build_check_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Checking your computer",
                                     description="ROS 2 needs a supported Ubuntu version, free disk space and internet.")
        page.add(group)
        self.check_rows = {}
        for key, title in [("os", "Ubuntu version"), ("disk", "Free disk space"),
                           ("net", "Internet connection"), ("ros", "ROS 2 already on this computer")]:
            row = Adw.ActionRow(title=title, subtitle="Checking…")
            group.add(row)
            self.check_rows[key] = [row, None]
        self.replace_page("check", page)

        osr = os_release()
        name = osr.get("PRETTY_NAME", "Unknown system")
        if self.distro:
            self.set_check("os", "ok", f"{name} → ROS 2 {self.distro.title()}")
        else:
            self.set_check("os", "error", f"{name} is not supported. Use Ubuntu 22.04, 24.04 or 26.04.")

        free_gb = shutil.disk_usage("/").free / 1e9
        if free_gb >= MIN_FREE_GB:
            self.set_check("disk", "ok", f"{free_gb:.0f} GB free")
        else:
            self.set_check("disk", "error", f"Only {free_gb:.1f} GB free. Free up at least {MIN_FREE_GB} GB, then reopen the installer.")

        found = sorted(os.listdir("/opt/ros")) if os.path.isdir("/opt/ros") else []
        others = [d.title() for d in found if d != self.distro]
        if others:
            self.set_check("ros", "warn", f"Found ROS 2 {', '.join(others)}. Use only one ROS 2 version per terminal.")
        elif found:
            self.set_check("ros", "ok", f"ROS 2 {self.distro.title()} is installed. You can add more parts.")
        else:
            self.set_check("ros", "ok", "None yet. This is a fresh install.")

        def check_net():
            try:
                socket.create_connection(("packages.ros.org", 80), timeout=5).close()
                result = ("ok", "packages.ros.org is reachable")
            except OSError:
                result = ("error", "Can't reach packages.ros.org. Connect to the internet, then reopen the installer.")
            GLib.idle_add(self.set_check, "net", *result)

        self.blockers.add("net")  # until the check finishes
        threading.Thread(target=check_net, daemon=True).start()

    def set_check(self, key, state, subtitle):
        row, icon = self.check_rows[key]
        if icon:
            row.remove(icon)
        self.check_rows[key][1] = status_icon(state)
        row.add_suffix(self.check_rows[key][1])
        row.set_subtitle(subtitle)
        (self.blockers.add if state == "error" else self.blockers.discard)(key)
        if self.stack.get_visible_child_name() == "check":
            self.next.set_sensitive(not self.blockers)

    # ---- 2. choose ----

    def build_choose_page(self):
        d = self.distro
        variants = CATALOG["base"]["variants"]
        goals = CATALOG["goals"]
        everything = [p for v in variants.values() for p in expand(v["packages"], d)] + \
                     [p for g in goals for p in expand(g["packages"], d)]
        have = installed(everything)
        # Before the repo is added apt knows no ROS packages, so assume everything exists.
        avail = parse_policy(run(["apt-cache", "policy", *everything])) if repo_configured() else None

        page = Adw.PreferencesPage()
        page.add(self.build_updates_group())
        base = Adw.PreferencesGroup(title="1. ROS 2 itself", description="Pick one. You can change this later.")
        page.add(base)
        self.variant_checks = {}
        first = None
        for vid, v in variants.items():
            check = Gtk.CheckButton(group=first, active=v.get("default", False))
            first = first or check
            row = Adw.ActionRow(title=v["title"], subtitle=v["description"])
            row.add_prefix(check)
            row.set_activatable_widget(check)
            if set(expand(v["packages"], d)) <= have:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label"]))
            base.add(row)
            self.variant_checks[vid] = check

        group = Adw.PreferencesGroup(title="2. What do you want to do?",
                                     description="Tick what you want. Press ⓘ to see exactly what each one installs.")
        page.add(group)
        self.have, self.avail, self.pkg_info = have, avail, {}
        threading.Thread(target=self.load_descriptions, args=(everything,), daemon=True).start()
        # A section is usable when its packages, and those of the sections it needs, are released.
        self.usable = {g["id"] for g in goals if avail is None
                       or {p for r in with_required([g["id"]]) for p in expand(r["packages"], d)} <= avail}
        self.cat_members, self.cat_checks = {}, {}
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
            row.add_prefix(Gtk.Image(icon_name=members[0].get("icon", "package-x-generic-symbolic")))
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            complete = usable and {p for g in usable for p in expand(g["packages"], d)} <= have
            if complete:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label"]))
            check.set_sensitive(bool(usable) and not complete)
            row.add_suffix(self.info_button(members))
            row.add_suffix(check)
            row.set_activatable_widget(check)
            group.add(row)
            self.cat_members[c["id"]], self.cat_checks[c["id"]] = members, check
        for cat, check in self.cat_checks.items():
            check.connect("toggled", self.on_category_toggled, cat)

        terminal = Adw.PreferencesGroup(title="3. Terminal")
        page.add(terminal)
        self.bashrc = Path.home() / ".bashrc"
        self.source_line = f"source /opt/ros/{d}/setup.bash"
        already = self.bashrc.exists() and self.source_line in self.bashrc.read_text()
        self.bashrc_check = Gtk.CheckButton(active=True, sensitive=not already)
        row = Adw.ActionRow(title="Load ROS 2 in every new terminal",
                            subtitle="Already set up" if already else f"Adds “{self.source_line}” to ~/.bashrc")
        row.add_prefix(self.bashrc_check)
        row.set_activatable_widget(self.bashrc_check)
        terminal.add(row)

        page.add(self.build_network_group(read_env()))

        self.manual = set()  # individually picked packages
        extra = Adw.PreferencesGroup(title="5. Individual packages",
                                     description="For when you know exactly which ROS 2 package you need.")
        self.manual_row = Adw.ActionRow(title="Pick individual packages", subtitle="Search every ROS 2 package",
                                        activatable=True)
        self.manual_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.manual_row.connect("activated", lambda _r: self.open_packages())
        extra.add(self.manual_row)
        page.add(extra)
        self.replace_page("choose", page)

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

    def info_button(self, goals):
        """ⓘ button whose popover lists what the given sections install."""
        button = Gtk.MenuButton(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="What's included", css_classes=["flat"])
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
            if "demo" in g:
                label(f"Try it: {for_distro(g['demo']['command'], d)}", "caption", margin_top=6)
        scroller = Gtk.ScrolledWindow(child=box, max_content_height=460, propagate_natural_height=True,
                                      propagate_natural_width=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        return Gtk.Popover(child=scroller)

    def build_updates_group(self):
        group = Adw.PreferencesGroup(title="Updates")
        self.updates = []
        self.update_check = Gtk.CheckButton(valign=Gtk.Align.CENTER, sensitive=False)  # ticked once updates are found
        self.update_row = Adw.ActionRow(title="Checking for ROS 2 updates…")
        self.update_row.add_suffix(self.update_check)
        self.update_row.set_activatable_widget(self.update_check)
        group.add(self.update_row)
        threading.Thread(target=lambda: GLib.idle_add(self.show_updates, upgradable(self.distro)), daemon=True).start()

        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=update_notifications_on())
        row = Adw.ActionRow(title="Notify me when ROS 2 updates are ready",
                            subtitle="Checks once a day and shows a desktop notification.")
        row.add_suffix(switch)
        row.set_activatable_widget(switch)

        def on_switch(sw, _param):
            if not set_update_notifications(sw.get_active()):
                row.set_subtitle("Couldn't set up the daily check (systemd user session not available).")
        switch.connect("notify::active", on_switch)
        group.add(row)
        return group

    def show_updates(self, pkgs):
        self.updates = pkgs
        n = len(pkgs)
        if n:
            short = [p.removeprefix(f"ros-{self.distro}-") for p in pkgs]
            self.update_row.set_title(f"Update {n} ROS 2 package{'s' * (n > 1)}")
            self.update_row.set_subtitle(", ".join(short[:6]) + (f" and {n - 6} more" if n > 6 else ""))
        else:
            self.update_row.set_title("ROS 2 is up to date")
            self.update_row.set_subtitle("No updates for your installed ROS 2 packages.")
        self.update_check.set_sensitive(bool(n))
        self.update_check.set_active(bool(n))

    def updates_picked(self):
        return self.updates if self.update_check.get_active() else []

    def build_network_group(self, saved):
        group = Adw.PreferencesGroup(
            title="4. Network",
            description="Leave these alone unless your teacher or lab says otherwise. "
                        "Computers and robots only see each other when these match.")
        self.domain = Gtk.SpinButton.new_with_range(0, 101, 1)
        self.domain.set_valign(Gtk.Align.CENTER)
        self.domain.set_value(int(saved.get("ROS_DOMAIN_ID", 0)))
        row = Adw.ActionRow(title="ROS Domain ID",
                            subtitle="Give each student or robot its own number (0–101) so they don't mix up.")
        row.add_suffix(self.domain)
        group.add(row)

        self.rmw_options = CATALOG["network"]["rmw"]
        ids = [o["id"] for o in self.rmw_options]
        self.rmw = Adw.ComboRow(title="Middleware (RMW)",
                                model=Gtk.StringList.new([o["title"] for o in self.rmw_options]))
        self.rmw.connect("notify::selected", lambda r, _p: r.set_subtitle(self.rmw_options[r.get_selected()]["description"]))
        saved_rmw = saved.get("RMW_IMPLEMENTATION")
        self.rmw.set_selected(ids.index(saved_rmw) if saved_rmw in ids else 0)
        self.rmw.set_subtitle(self.rmw_options[self.rmw.get_selected()]["description"])
        group.add(self.rmw)

        self.localhost = Gtk.Switch(valign=Gtk.Align.CENTER,
                                    active="ROS_LOCALHOST_ONLY" in saved or "ROS_AUTOMATIC_DISCOVERY_RANGE" in saved)
        row = Adw.ActionRow(title="This computer only",
                            subtitle="Keep ROS 2 traffic off the network. Good for simulation-only classes.")
        row.add_suffix(self.localhost)
        row.set_activatable_widget(self.localhost)
        group.add(row)
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
        if self.updates_picked():
            titles.append(f"Update {len(self.updates_picked())} ROS 2 packages")
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
        check.set_active(pkg in self.manual or pkg in self.pkg_installed)
        check.set_sensitive(pkg not in self.pkg_installed)
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
        upgrades = self.updates_picked()
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
            result = plan_install(pkgs + upgrades)
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

    def start_install(self):
        _titles, pkgs = self.selection()
        self.upgrades = self.updates_picked()
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
        self.retry = Gtk.Button(label="Try again", visible=False, halign=Gtk.Align.CENTER,
                                css_classes=["pill", "suggested-action"])
        self.retry.connect("clicked", lambda _b: self.start_install())
        for w in (self.status, self.progress, self.details, self.retry):
            page.append(w)
        self.replace_page("install", page)
        self.show_page("install")
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
            GLib.idle_add(self.on_line, "##STEP Updating rosdep\n")
            if self.stream(["bash", "-c", "command -v rosdep >/dev/null && rosdep update || true"]):
                GLib.idle_add(self.on_line, "rosdep update failed; run it later in a terminal.\n")
            if self.want_bashrc:
                with self.bashrc.open("a") as f:
                    f.write(f"\n# Added by ROS 2 Installer\n{self.source_line}\n")
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
        buf.insert(buf.get_end_iter(), line)
        self.log.scroll_mark_onscreen(self.log_end)
        if line.startswith("##STEP "):
            self.status.set_label(line[7:].strip() + "…")
            self.progress.set_fraction(0)  # each apt run (install, then updates) fills the bar again
            self.saw_download = False
        elif self.progress.get_fraction() == 0:
            self.progress.pulse()  # e.g. "apt update", before apt reports real progress

    def on_finished(self, code):
        if code == 0:
            self.build_done_page()
            return
        if code in (126, 127):  # pkexec: prompt dismissed / not authorized
            self.status.set_label("The password prompt was closed, so nothing was installed.")
        else:
            self.status.set_label("Something went wrong. Open “Show details” to see why, then try again.")
            self.details.set_expanded(True)
        self.retry.set_visible(True)
        self.back.set_visible(True)

    # ---- 5. done / try it ----

    def build_done_page(self):
        d = self.distro
        status = Adw.StatusPage(icon_name="emblem-ok-symbolic", title=f"ROS 2 {d.title()} is ready",
                                description="Open a new terminal window so it loads ROS 2, or try a demo below.")
        group = Adw.PreferencesGroup(title="Try it")
        demos = [CATALOG["base"]["demo"]] + [g["demo"] for g in CATALOG["goals"] if "demo" in g
                                             and set(expand(g["packages"], d)) <= installed(expand(g["packages"], d))]
        for demo in demos:
            command = for_distro(demo["command"], d)
            row = Adw.ActionRow(title=demo["title"], subtitle=command)
            button = Gtk.Button(label="Run", valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _b, cmd=command: self.try_demo(cmd))
            row.add_suffix(button)
            group.add(row)
        status.set_child(Adw.Clamp(child=group))
        self.replace_page("done", status)
        self.show_page("done")

    def try_demo(self, command):
        script = f"source /opt/ros/{self.distro}/setup.bash; {ENV_SOURCE_LINE}; {command}; exec bash"
        if shutil.which("gnome-terminal"):
            subprocess.Popen(["gnome-terminal", "--", "bash", "-c", script])
        else:
            # shortcut: assumes the terminal accepts "-e cmd args"; add per-terminal flags if one doesn't
            subprocess.Popen(["x-terminal-emulator", "-e", "bash", "-c", script])


def main():
    if "--check-updates" in sys.argv:
        check_updates()
        return 0
    if os.geteuid() == 0:
        sys.exit("Run the installer without sudo; it asks for your password when it needs it.")
    app = Adw.Application(application_id="org.ros2.GuiInstaller")
    app.connect("activate", lambda a: Installer(a).present())
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
