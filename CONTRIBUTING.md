# Contributing

Thanks for helping! Bug reports, new sections and fixes are all welcome.
[Open an issue](https://github.com/runtimerobotics/ros2_gui_installer/issues/new/choose) first if you're not sure.

## Run it from a clone

```bash
git clone https://github.com/runtimerobotics/ros2_gui_installer.git
cd ros2_gui_installer
./ros2_installer.py
```

It needs `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml` (see *Desktops and requirements* in the README).

## Add a section

Sections live in [`catalog.yaml`](catalog.yaml); most additions need no Python changes.

```yaml
  - id: foxglove                      # unique, lowercase
    category: connect                 # one of the ids under "categories"
    title: Foxglove                   # short, shown on the row
    icon: network-wireless-symbolic   # a symbolic icon name from the Adwaita theme
    description: "What it lets a beginner do, first. Then the technical names."
    requires: [simulation]            # optional: other sections it needs
    packages:
      - ros-{distro}-foxglove-bridge  # quote names with {distro} inside [ ]
    demo:                             # optional: a command the last page can run
      title: Foxglove bridge
      command: ros2 launch foxglove_bridge foxglove_bridge_launch.xml
```

- `{distro}` becomes `humble`, `jazzy` or `lyrical`.
- When a version needs something different, use a map: `packages: {default: [...], humble: [...]}`. `humble-arm64:` overrides one version on one processor type, and a demo `command: null` means no demo there.
- If a section isn't released for a version yet, add `not_released_for: [lyrical]`. CI then fails as soon as it is released, so you know to remove the marker.
- Write descriptions for someone on their first day with ROS 2: what it does for them, then the package names.

## Before you open a pull request

```bash
python3 test_installer.py
python3 -m pyflakes ros2_installer.py test_installer.py
shellcheck --severity=warning helper.sh install.sh packaging/build-deb.sh
```

`test_installer.py` also checks that every package in the catalog exists in apt for the ROS 2 version on your computer. CI runs it on Ubuntu 22.04, 24.04 and 26.04 (amd64 and arm64), and installs turtlesim through the real root helper.

- Keep `helper.sh` small and strict: it is the only part that runs as root, and it must refuse anything that isn't a ROS 2 package name.
- Code style follows what's there: plain functions for logic that can be tested, the `Installer` window for GTK, and short comments that say why.
- Changes users will notice go in [`CHANGELOG.md`](CHANGELOG.md).

## Releases

The maintainer bumps `__version__` in `ros2_installer.py`, tags `vX.Y.Z` and publishes a GitHub release. The release workflow builds the `.deb` and attaches it.

By contributing, you agree your work is licensed under the [Apache License 2.0](LICENSE).
