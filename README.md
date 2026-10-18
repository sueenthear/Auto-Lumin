# Auto Lumin

Windows automation utilities for binding a target application window, capturing images, matching templates, and replaying input actions.

## Components

- `script_core/`: reusable Python API for window, keyboard, mouse, image, pixel, and action-sequence operations.
- `dev_tool/ok_lumin_tool/`: desktop utility for window positioning, screenshots, and recording or replaying actions.
- `example/`: minimal API example that inspects a window and optionally saves a screenshot without sending keyboard or mouse input.
- `tests/`: unit tests.

## Setup

See [agent-read-this-to-setup-environment.md](agent-read-this-to-setup-environment.md) for Windows prerequisites, virtual environment setup, dependencies, tests, and the desktop tool launch command.

Core API details are in [script_core/use.md](script_core/use.md). The desktop tool has its own [README](dev_tool/README.md).

## Safety

The automation API can send real keyboard and mouse events to the bound application. Review any script before running it and confirm the target window. The included example does not send input events.
