# Changelog

What changed in each release. Full notes and the `.deb` for each version are on the
[releases page](https://github.com/runtimerobotics/ros2_gui_installer/releases).

## Unreleased
- The progress bar keeps moving while the installer connects to the ROS 2 server and refreshes package lists. On a fresh computer this takes a few minutes, and the bar used to look frozen.

## 0.2.5 (2026-10-28)
- `--profile` while the installer is already open fills in that window instead of doing nothing; opening the installer again brings its window forward instead of a second one.
- New profile `profiles/ros2-workshop.yaml`: the ROS 2 packages of the Runtime Robotics ROS 2 workshop.

## 0.2.4 (2026-10-28)
- After a failed update from **Update ROS 2 packages…**, **Back** goes to the choose page instead of an old or missing review page.
- After an update, the last page says *Your ROS 2 packages are up to date* instead of *Everything is installed*.

## 0.2.3 (2026-10-28)
- **☰ → Create profile…** opens the profile editor in its own window. Everything can be ticked there, because it only writes a file. **Save…** closes it and offers **Use it here**; **Cancel** closes it without saving.
- **☰ → Update ROS 2 packages…** is a dialog that lists waiting updates, installs them with **Update now**, and holds the daily notification switch. The Updates group is gone from the choose page.
- An update-only run leaves `.bashrc` and network settings alone.

## 0.2.2 (2026-10-28)
- Save your choices, or this computer's setup, as a workshop profile.
- Loading a profile keeps installed categories untickable, and lists what's already installed.

## 0.2.1 (2026-10-27)
- Save and load workshop profiles from the **☰** menu.

## 0.2.0 (2026-10-27)
- **Workshop profiles:** a YAML file that picks what to install and the network settings, opened with `--profile <url or file>` or `PROFILE=` in the one-line install.

## 0.1.9 (2026-10-27)
- A notice on the first page when a newer installer is out.
- Install logs saved in `~/.local/state/ros2-gui-installer/`, with **Copy details** and **Open log** after a failure.
- Screen reader labels for info buttons, the step bar and status icons.
- Troubleshooting section in the README.

## 0.1.8 (2026-10-27)
- Step bar: *Check → Choose → Review → Install → Try it*.
- Desktop-Full is recommended and selected by default, then Desktop, then ROS-Base.

## 0.1.7 (2026-10-27)
- Last page: **Open terminal**, **Run** turtlesim, and every demo listed (greyed out when not installed).

## 0.1.6 (2026-10-27)
- Beginner wording throughout, a *What next?* section, and demos that say how to stop them.

## 0.1.5 (2026-10-27)
- ROS-Base option, ARM64 support (Jetson, Raspberry Pi), and processor, memory and graphics card on the first page.
- CI on real arm64 machines.

## 0.1.4 (2026-10-27)
- `.deb` package with its own password prompt (polkit), menu entry, icon and man page.
- Apache 2.0 license.

## 0.1.3 (2026-10-27)
- Waits for Ubuntu's own updates instead of failing, plain-language errors, **Check again**, network settings under *Advanced*, **Install more**.
- Stricter checks in the root helper.

## 0.1.2 (2026-10-27)
- Sections not released for your ROS 2 version are never offered.
- A broken PPA no longer stops the install; the window can't be closed mid-install; old ROS 2 sources are switched to `ros2-apt-source`.

## 0.1.1 (2026-10-27)
- The progress bar no longer drops back to 0 after apt finishes.

## 0.1.0 (2026-10-27)
- First public beta: a five-step wizard with 20 sections in 10 categories, individual packages, network settings, and update notifications, for Humble, Jazzy and Lyrical.
