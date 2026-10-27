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

## Run

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml
git clone https://github.com/runtimerobotics/ros2_gui_installer.git
cd ros2_gui_installer && ./ros2_installer.py
```

Run it as your normal user, without `sudo`. When it needs admin rights, it asks for your password once through `pkexec`.

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
