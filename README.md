# ROS 2 GUI Installer

[![CI](https://github.com/runtimerobotics/ros2_gui_installer/actions/workflows/ci.yml/badge.svg)](https://github.com/runtimerobotics/ros2_gui_installer/actions/workflows/ci.yml)
<br>
[![Ubuntu 22.04](https://img.shields.io/badge/Ubuntu-22.04-E95420?logo=ubuntu&logoColor=white)](https://releases.ubuntu.com/22.04/)
[![Ubuntu 24.04](https://img.shields.io/badge/Ubuntu-24.04-E95420?logo=ubuntu&logoColor=white)](https://releases.ubuntu.com/24.04/)
[![Ubuntu 26.04](https://img.shields.io/badge/Ubuntu-26.04-E95420?logo=ubuntu&logoColor=white)](https://releases.ubuntu.com/26.04/)
<br>
[![ROS 2 Humble](https://img.shields.io/badge/ROS%202-Humble-22314E?logo=ros&logoColor=white)](https://docs.ros.org/en/humble/)
[![ROS 2 Jazzy](https://img.shields.io/badge/ROS%202-Jazzy-22314E?logo=ros&logoColor=white)](https://docs.ros.org/en/jazzy/)
[![ROS 2 Lyrical](https://img.shields.io/badge/ROS%202-Lyrical-22314E?logo=ros&logoColor=white)](https://docs.ros.org/en/lyrical/)
<br>
[![Python 3](https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GTK 4](https://img.shields.io/badge/GTK-4-4A86CF?logo=gtk&logoColor=white)](https://www.gtk.org/)
[![libadwaita](https://img.shields.io/badge/libadwaita-1.1%2B-4A86CF?logo=gnome&logoColor=white)](https://gnome.pages.gitlab.gnome.org/libadwaita/)
[![Install: one line](https://img.shields.io/badge/install-curl%20%7C%20bash-2ea44f?logo=gnubash&logoColor=white)](#install)
[![Last commit](https://img.shields.io/github/last-commit/runtimerobotics/ros2_gui_installer)](https://github.com/runtimerobotics/ros2_gui_installer/commits/main)

A beginner-friendly installer for ROS 2 on Ubuntu, built with GTK4 and libadwaita.
It walks you through five steps: **system check → choose → review → install → try it**.

Created by **Lentin Joseph** · [Runtime Robotics](https://runtimerobotics.com)

| Ubuntu | ROS 2 |
|---|---|
| 22.04 | Humble |
| 24.04 | Jazzy |
| 26.04 | Lyrical |

## What you can install

First pick **ROS 2 itself**: Desktop or Desktop-Full, plus `ros-dev-tools` and `rosdep`.
Then tick any of these categories. Each one installs all the sections in it:

| Category | Section | Main packages |
|---|---|---|
| **Learn ROS 2** | Tutorials and examples | `turtlesim`, minimal C++/Python nodes, action and TF2 tutorials, lifecycle demo |
| **Simulate** | Simulate robots | Gazebo, `ros_gz`, `ros2_control` |
| **Robot models** | Describe your own robot (URDF) | `urdf_tutorial`, `xacro`, `robot_state_publisher`, `joint_state_publisher_gui` |
| **See and record** | Visualize and debug | rqt plugins, `rqt_tf_tree`, `tf2_tools`, diagnostics, `rviz_visual_tools` |
| | Record and replay data | `rosbag2` + MCAP, `rqt_bag`, PlotJuggler |
| **Sensors and cameras** | Use cameras | `vision_opencv`, `image_pipeline`, `v4l2_camera`, RealSense |
| | Markers and AprilTags | `apriltag_ros`, `aruco_opencv` |
| | Use LiDAR, IMU and GPS | Velodyne, Hokuyo (`urg_node`), `imu_tools`, `nmea_navsat_driver`, `pcl_ros` |
| **Mobile robots and navigation** | Drive a mobile robot | TurtleBot 4 simulation, SLAM Toolbox |
| | Navigate on its own | Nav2 |
| | Drive with keyboard or gamepad | `teleop_twist_keyboard`, `teleop_twist_joy`, `joy` |
| | Know where the robot is | `robot_localization`, AMCL, Cartographer |
| | TurtleBot 3 | `turtlebot3`, Gazebo worlds |
| | 3D mapping | RTAB-Map |
| **Robot arms** | Control a robot arm | Universal Robots in Gazebo |
| | Plan arm motion | MoveIt 2 with the Panda demo |
| | More arm tools | MoveIt Servo, `moveit_py`, CHOMP, `pick_ik`, Robotiq gripper |
| **Behavior and task logic** | Behavior trees and state machines | BehaviorTree.CPP, `py_trees_ros` + tutorials, SMACH, FlexBE |
| **Connect to other tools** | Connect from a browser or other tools | rosbridge, Foxglove bridge |
| **Developer tools** | Build, test and debug your own packages | ament linters, clang-format, launch testing, `generate_parameter_library`, tracing, SROS2 |

Each category is one row with a tick box on the right. When everything in a category is already installed, the row says **Installed**.
Press **ⓘ** on a category or section to see exactly what it installs: every package with its description, whether you already have it, and the demo command.

Some sections need another one: Nav2, both TurtleBots and the arm need Simulate robots, and More arm tools needs MoveIt 2.
Ticking a section ticks what it needs.
If a section isn't released yet for your ROS 2 version, it shows greyed out.
When a package is missing for one version (for example PlotJuggler or Cartographer on Lyrical), that version gets the rest of the section without it.
After installing, the **Try it** page opens a terminal running a demo for each section you installed.

### Updates

The **Updates** group at the top of the choose page shows how many of your installed ROS 2 packages have updates waiting. Tick it to update them along with whatever else you install.
The installer updates only packages you already have, with `apt-get install --only-upgrade`, so it doesn't change which packages apt marks as manually installed.

Turn on **Notify me when ROS 2 updates are ready** to get a desktop notification when updates are waiting.
This sets up a daily systemd user timer (`ros2-gui-installer-updates.timer`) that runs `ros2_installer.py --check-updates`.
The check reads the package lists from Ubuntu's own daily apt refresh, so it never asks for a password.
Turn the switch off to remove the timer. You can also run the check by hand:

```bash
~/.local/bin/ros2-gui-installer --check-updates
```

### Individual packages

Already know which package you need? Open **5. Individual packages → Pick individual packages** on the choose page.
It lists every `ros-<distro>-*` package in the ROS 2 repository (about 2,000 for Jazzy, without debug-symbol packages).
Search by any word in the name or description (`imu`, `lidar`, `moveit servo`), then tick the packages you want.
Packages you already have are marked **Installed**.
The packages you pick go into the same install as the sections above.

### Network settings (for classrooms and labs)

The installer can also change three network settings:

- **ROS Domain ID (0–101):** give each student or robot its own number so they don't see each other's topics.
- **Middleware (RMW):** Default (Fast DDS), Cyclone DDS or Zenoh. The installer installs the package for the one you pick.
- **This computer only:** keeps ROS 2 traffic on this computer. On Humble it sets `ROS_LOCALHOST_ONLY=1`; on newer versions it sets `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`.

The settings are saved to `~/.config/ros2-gui-installer/env.sh`, and `~/.bashrc` loads that file, so they apply to every new terminal.
To change them, run the installer again; it starts from the values you saved last time.

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

[GitHub Actions](.github/workflows/ci.yml) runs the same test in Ubuntu 22.04, 24.04 and 26.04 containers with the ROS 2 apt repository added. So every push checks the catalog against Humble, Jazzy and Lyrical.
It also runs every Monday, to catch packages that get removed from apt.
If a section is expected to be missing for a version, mark it with `not_released_for: [<distro>]` in `catalog.yaml`. The test then fails as soon as that section's packages are released, so you know to remove the marker.

## About

ROS 2 Installer is created and maintained by **Lentin Joseph** at [Runtime Robotics](https://runtimerobotics.com).
Found a bug or have an idea? [Open an issue](https://github.com/runtimerobotics/ros2_gui_installer/issues).
In the app, open the **☰** menu → **About ROS 2 Installer**.

ROS and ROS 2 are trademarks of Open Robotics. This installer is an independent project, not affiliated with or endorsed by Open Robotics.
