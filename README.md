# ROS 2 GUI Installer

A beginner-friendly installer for ROS 2 on Ubuntu, built with GTK4 and libadwaita.
It walks you through five steps: **system check → choose → review → install → try it**.

| Ubuntu | ROS 2 |
|---|---|
| 22.04 | Humble |
| 24.04 | Jazzy |
| 26.04 | Lyrical |

## What you can install

- **ROS 2 itself:** Desktop or Desktop-Full, plus `ros-dev-tools` and `rosdep`
- **Simulate robots:** Gazebo, `ros_gz`, `ros2_control`
- **Drive a mobile robot:** TurtleBot 4 simulation, SLAM Toolbox
- **Navigate on its own:** Nav2
- **Control a robot arm:** Universal Robots in Gazebo
- **Plan arm motion:** MoveIt 2 with the Panda demo

If a section isn't released yet for your ROS 2 version, it shows greyed out.
After installing, the **Try it** page opens a terminal running a demo for each section you installed.

## Install

Open a terminal (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd>) and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/runtimerobotics/ros2_gui_installer/main/install.sh | bash
```

This command:

1. installs the few Ubuntu packages the GUI needs (GTK4, libadwaita, PyYAML), asking for your password if any are missing
2. downloads the installer to `~/.local/share/ros2-gui-installer`
3. adds **ROS 2 Installer** to your app menu and a `ros2-gui-installer` command
4. opens the installer

Run it as your normal user, without `sudo`. The installer asks for your password itself, once, right before it installs ROS 2.

If you'd rather read the script before running it:

```bash
curl -fsSLO https://raw.githubusercontent.com/runtimerobotics/ros2_gui_installer/main/install.sh
less install.sh
bash install.sh
```

### Open it later

Pick **ROS 2 Installer** from the app menu, or run `~/.local/bin/ros2-gui-installer`.
You can run it again any time to add more sections.

### Update

Run the same `curl … | bash` command again. It replaces the old copy with the latest version.

### Uninstall the GUI

This removes only the installer app, not ROS 2:

```bash
rm -rf ~/.local/share/ros2-gui-installer ~/.local/bin/ros2-gui-installer ~/.local/share/applications/ros2-gui-installer.desktop
```

### Run from a git clone (for development)

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml
git clone https://github.com/runtimerobotics/ros2_gui_installer.git
cd ros2_gui_installer && ./ros2_installer.py
```

## How it works

- `catalog.yaml` lists every section with its packages and demo command.
  To add a section, add an entry there; you don't need to change any code.
- `ros2_installer.py` is the GUI. It runs as your normal user.
- `helper.sh` is the only part that runs as root. It refuses any package name that isn't a ROS 2 package. If the ROS 2 apt repository is missing, it adds it using the official [`ros2-apt-source`](https://github.com/ros-infrastructure/ros-apt-source) package, then runs `apt-get install`.

## Test

```bash
./test_installer.py
```

The test checks the parsers and the helper's name filter. It also checks that every catalog package exists in apt for the ROS 2 version on this machine.
