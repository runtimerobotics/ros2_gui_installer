# ROS 2 GUI Installer

A beginner-friendly installer for ROS 2 on Ubuntu, built with GTK4 and libadwaita.
It walks you through five steps: **system check → choose → review → install → try it**.

| Ubuntu | ROS 2 |
|---|---|
| 22.04 | Humble |
| 24.04 | Jazzy |
| 26.04 | Lyrical |

## What you can install

First pick **ROS 2 itself**: Desktop or Desktop-Full, plus `ros-dev-tools` and `rosdep`.
Then add any of these sections, grouped into collapsible categories:

| Category | Section | Main packages |
|---|---|---|
| **Simulate** | Simulate robots | Gazebo, `ros_gz`, `ros2_control` |
| **Robot models** | Describe your own robot (URDF) | `urdf_tutorial`, `xacro`, `robot_state_publisher`, `joint_state_publisher_gui` |
| **See and record** | Visualize and debug | rqt plugins, `rqt_tf_tree`, `tf2_tools`, diagnostics, `rviz_visual_tools` |
| | Record and replay data | `rosbag2` + MCAP, `rqt_bag`, PlotJuggler |
| **Sensors and cameras** | Use cameras | `vision_opencv`, `image_pipeline`, `v4l2_camera`, RealSense |
| | Use LiDAR, IMU and GPS | Velodyne, Hokuyo (`urg_node`), `imu_tools`, `nmea_navsat_driver`, `pcl_ros` |
| **Mobile robots and navigation** | Drive a mobile robot | TurtleBot 4 simulation, SLAM Toolbox |
| | Navigate on its own | Nav2 |
| | Drive with keyboard or gamepad | `teleop_twist_keyboard`, `teleop_twist_joy`, `joy` |
| | Know where the robot is | `robot_localization`, AMCL, Cartographer |
| **Robot arms** | Control a robot arm | Universal Robots in Gazebo |
| | Plan arm motion | MoveIt 2 with the Panda demo |
| | More arm tools | MoveIt Servo, `moveit_py`, CHOMP, `pick_ik`, Robotiq gripper |
| **Connect to other tools** | Connect from a browser or other tools | rosbridge, Foxglove bridge |

Some sections need another one: Nav2, the mobile robot and the arm need Simulate robots, and More arm tools needs MoveIt 2.
Ticking a section ticks what it needs.
If a section isn't released yet for your ROS 2 version, it shows greyed out.
When a package is missing for one version (for example PlotJuggler or Cartographer on Lyrical), that version gets the rest of the section without it.
After installing, the **Try it** page opens a terminal running a demo for each section you installed.

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
