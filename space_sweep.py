#!/usr/bin/env python3
"""A careful, dependency-free macOS disk-space inspector and cleaner.

Run with: python3 space_sweep.py
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HOME = Path.home()
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[38;5;203m"
GREEN = "\x1b[38;5;114m"
YELLOW = "\x1b[38;5;222m"
BLUE = "\x1b[38;5;117m"
PINK = "\x1b[38;5;218m"
PURPLE = "\x1b[38;5;141m"
TEAL = "\x1b[38;5;80m"
ORANGE = "\x1b[38;5;215m"
GRAPH_COLORS = (PINK, BLUE, GREEN, YELLOW, PURPLE, TEAL, ORANGE)
RULE = "─" * 64

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "space-sweep" / "config.json"
DEFAULT_PROJECT_ROOTS = ("Developer", "Projects", "Code", "dev", "src", "workspace")
SKIP_DIRS = {"node_modules", "Library", "Pods", ".git", ".Trash"}
MAX_PROJECT_DEPTH = 3


def expand(value: str) -> Path:
    return Path(value).expanduser()


def is_too_broad(path: Path) -> bool:
    """True for paths that must never be deleted wholesale (home, its parents, core folders)."""
    resolved = path.resolve()
    core = {HOME / name for name in ("Library", "Documents", "Desktop", "Applications")}
    return resolved == Path(resolved.anchor) or resolved == HOME or resolved in HOME.parents or resolved in core


def config_paths(config: dict, key: str) -> tuple[Path, ...]:
    values = config.get(key, ())
    if not isinstance(values, list):
        return ()
    paths = []
    for value in values:
        if not isinstance(value, str):
            continue
        path = expand(value)
        if is_too_broad(path):
            print(color(f"Ignoring {value} in {key}: too broad to delete safely.", YELLOW))
            continue
        paths.append(path)
    return tuple(paths)


def load_config() -> dict:
    """Optional user settings from ~/.config/space-sweep/config.json.

    {
      "project_roots": ["~/work"],        # where to look for Flutter projects
      "extra_paths": ["~/Movies/exports"],  # extra folders offered as safe to clean
      "careful_paths": ["~/Library/Application Support/Foo"],  # never in one-touch
      "exclude": ["wallpapers", "local-models"]   # category keys to hide
    }
    """
    try:
        with CONFIG_PATH.open() as source:
            config = json.load(source)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        print(color(f"Ignoring {CONFIG_PATH}: {error}", YELLOW))
        return {}
    return config if isinstance(config, dict) else {}


def color(value: str, tone: str) -> str:
    if sys.stdout.isatty():
        return f"{tone}{value}{RESET}"
    return value


def bytes_to_text(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit != "B":
                return f"{size:.1f} {unit}"
            return f"{int(size)} B"
        size /= 1024
    return "0 B"


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    result = subprocess.run(
        ["du", "-sk", str(path)], text=True, capture_output=True, check=False
    )
    try:
        return int(result.stdout.split()[0]) * 1024
    except (IndexError, ValueError):
        return 0


def data_volume() -> tuple[int, int]:
    result = subprocess.run(
        ["df", "-k", "/System/Volumes/Data"], text=True, capture_output=True, check=False
    )
    fields = result.stdout.splitlines()[-1].split()
    return int(fields[1]) * 1024, int(fields[3]) * 1024


def bar(value: int, maximum: int, width: int = 28) -> str:
    filled = max(0, min(width, round(value / maximum * width))) if maximum else 0
    return color("█" * filled, BLUE) + color("░" * (width - filled), DIM)


class ScanStatus:
    """A tiny spinner so potentially slow disk walks never feel frozen."""

    frames = ("◜", "◠", "◝", "◡", "◟", "◞")

    def __init__(self, message: str = "Scanning your removable space") -> None:
        self.message = message
        self.done = threading.Event()
        self.thread = None

    def __enter__(self) -> "ScanStatus":
        clear_screen()
        print(color("SPACE SWEEP", PINK) + color("  macOS storage, made legible", DIM))
        print(color(RULE, DIM))
        if not sys.stdout.isatty():
            print(f"{self.message}…")
            return self

        def spin() -> None:
            index = 0
            while not self.done.is_set():
                frame = self.frames[index % len(self.frames)]
                print(
                    f"\r{color(frame, PINK)} {self.message}… "
                    f"{color('this can take a moment', DIM)}",
                    end="",
                    flush=True,
                )
                index += 1
                time.sleep(0.12)

        self.thread = threading.Thread(target=spin, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.done.set()
        if self.thread:
            self.thread.join()
        if sys.stdout.isatty():
            print("\r\x1b[2K", end="", flush=True)


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    description: str
    paths: tuple[Path, ...]
    safe: bool = True
    contents_only: bool = False
    cleanup: Callable[[], tuple[list[Path], list[str]]] | None = None
    auto_cleanup: Callable[[], tuple[list[Path], list[str]]] | None = None

    def size(self) -> int:
        return sum(path_size(path) for path in self.paths)


def remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def direct_cleanup(
    paths: tuple[Path, ...], contents_only: bool = False
) -> Callable[[], tuple[list[Path], list[str]]]:
    """Delete the paths, or only what is inside them so the folder itself survives."""

    def clean() -> tuple[list[Path], list[str]]:
        removed: list[Path] = []
        errors: list[str] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                targets = list(path.iterdir()) if contents_only and path.is_dir() else [path]
            except OSError as error:
                errors.append(f"{path}: {error.strerror or error}")
                continue
            for target in targets:
                try:
                    remove(target)
                    removed.append(target)
                except OSError as error:
                    errors.append(f"{target}: {error.strerror or error}")
        return removed, errors

    return clean


def cleaner_for(item: Category, automatic: bool = False) -> Callable[[], tuple[list[Path], list[str]]]:
    custom = (item.auto_cleanup if automatic else None) or item.cleanup
    return custom or direct_cleanup(item.paths, item.contents_only)


@dataclass(frozen=True)
class Simulator:
    folder: Path
    udid: str
    name: str
    runtime: str
    last_used: float

    @property
    def runtime_label(self) -> str:
        return self.runtime.replace("com.apple.CoreSimulator.SimRuntime.", "")


def simulator_devices() -> list[Simulator]:
    """Installed simulators, most recently used first.

    device.plist is rewritten whenever a device boots or shuts down, so its
    modification time is the best "last used" signal available.
    """
    root = HOME / "Library/Developer/CoreSimulator/Devices"
    devices = []
    for plist_path in root.glob("*/device.plist"):
        try:
            with plist_path.open("rb") as source:
                info = plistlib.load(source)
            last_used = plist_path.stat().st_mtime
        except (OSError, plistlib.InvalidFileException):
            continue
        devices.append(
            Simulator(
                plist_path.parent,
                plist_path.parent.name,
                info.get("name", "Unknown"),
                info.get("runtime", "Unknown"),
                last_used,
            )
        )
    return sorted(devices, key=lambda device: device.last_used, reverse=True)


def delete_simulators(devices: list[Simulator]) -> tuple[list[Path], list[str]]:
    removed: list[Path] = []
    errors: list[str] = []
    for device in devices:
        result = subprocess.run(
            ["xcrun", "simctl", "delete", device.udid],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            removed.append(device.folder)
            continue
        errors.append(
            f"{device.name}: Simulator service could not remove it "
            f"({result.stderr.strip() or 'unknown error'})"
        )
    return removed, errors


def clean_simulators_auto() -> tuple[list[Path], list[str]]:
    """Non-interactive: keep only the most recently used simulator."""
    devices = simulator_devices()
    if not devices:
        return [], ["No simulator devices were found."]
    return delete_simulators(devices[1:])


def clean_simulators() -> tuple[list[Path], list[str]]:
    """Interactive: the last used simulator is kept by default; toggle any others."""
    devices = simulator_devices()
    if not devices:
        return [], ["No simulator devices were found."]

    sizes = [path_size(device.folder) for device in devices]
    keep = {0}
    while True:
        print(color("\nSIMULATORS", BOLD) + color("  (newest first)", DIM))
        print(f"{'#':<4}{'DEVICE':<22}{'RUNTIME':<14}{'SIZE':>10}  {'LAST USED':<17} ACTION")
        print(color(RULE, DIM))
        for index, device in enumerate(devices):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(device.last_used))
            action = color("KEEP", GREEN) if index in keep else color("DELETE", RED)
            print(
                f"{index + 1:<4}{device.name[:21]:<22}{device.runtime_label[:13]:<14}"
                f"{bytes_to_text(sizes[index]):>10}  {when:<17} {action}"
            )
        doomed = [i for i in range(len(devices)) if i not in keep]
        freed = sum(sizes[i] for i in doomed)
        print(color(RULE, DIM))
        print(f"Deleting {len(doomed)} of {len(devices)}, freeing {color(bytes_to_text(freed), YELLOW)}")
        print(color("Numbers toggle keep/delete (e.g. 2 3) · [Enter] delete as shown · [q] cancel", DIM))
        try:
            answer = input(color("› ", PINK)).strip().lower()
        except EOFError:
            return [], []
        if answer == "q":
            return [], []
        if answer == "":
            if not doomed:
                return [], []
            return delete_simulators([devices[i] for i in doomed])
        for token in answer.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(devices):
                keep ^= {int(token) - 1}


def flutter_artifacts(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """build/ and .dart_tool/ of every Flutter project found under the roots."""
    targets: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if (folder / "pubspec.yaml").exists():
            targets.extend(
                folder / name for name in ("build", ".dart_tool") if (folder / name).exists()
            )
            return
        if depth >= MAX_PROJECT_DEPTH:
            return
        try:
            children = sorted(folder.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir() and not child.is_symlink() and child.name not in SKIP_DIRS and not child.name.startswith("."):
                walk(child, depth + 1)

    for root in roots:
        if root.is_dir():
            walk(root, 0)
    return tuple(dict.fromkeys(targets))


def categories() -> list[Category]:
    config = load_config()
    roots = config_paths(config, "project_roots") or tuple(HOME / name for name in DEFAULT_PROJECT_ROOTS)
    xcode_support = HOME / "Library/Developer/Xcode/iOS DeviceSupport"
    xcode_derived = HOME / "Library/Developer/Xcode/DerivedData"
    xcode_archives = HOME / "Library/Developer/Xcode/Archives"
    cache_paths = (HOME / ".cache", HOME / "Library/Caches")
    android_paths = (
        HOME / "Library/Android/sdk/ndk",
        HOME / "Library/Android/sdk/system-images",
        HOME / "Library/Android/sdk/emulator",
    )
    extra_paths = config_paths(config, "extra_paths")
    careful_paths = config_paths(config, "careful_paths")

    found = [
        Category(
            "xcode-support",
            "Xcode device support",
            "Downloaded symbols for physical iOS devices; Xcode can fetch them again.",
            (xcode_support,),
        ),
        Category(
            "xcode-derived",
            "Xcode build data",
            "DerivedData: intermediate build products Xcode regenerates.",
            (xcode_derived,),
        ),
        Category(
            "xcode-archives",
            "Xcode archives",
            "Archived app builds and their dSYMs. May be the only copy of a released build.",
            (xcode_archives,),
            safe=False,
        ),
        Category(
            "simulators",
            "Simulator devices",
            "Lets you pick which simulators to delete (default: keep the last used). "
            "One-touch keeps only the last used.",
            (HOME / "Library/Developer/CoreSimulator/Devices",),
            cleanup=clean_simulators,
            auto_cleanup=clean_simulators_auto,
        ),
        Category(
            "caches",
            "Developer & app caches",
            "Contents of ~/.cache and ~/Library/Caches (Hugging Face, npm, browsers, app caches). "
            "Apps rebuild them; some may redownload or sign in again.",
            cache_paths,
            contents_only=True,
        ),
        Category(
            "android",
            "Android SDK extras",
            "NDK, emulator, and system images. Android Studio can reinstall selected packages.",
            android_paths,
        ),
        Category(
            "flutter",
            "Flutter build artifacts",
            "build/ and .dart_tool/ in Flutter projects found under your project folders.",
            flutter_artifacts(roots),
        ),
        Category(
            "local-models",
            "Local AI models",
            "Models downloaded by LM Studio. Chats and settings are kept.",
            (HOME / ".lmstudio/models",),
        ),
        Category(
            "dart",
            "Dart & Pub data",
            "Dart analysis server and Pub package cache. Globally activated Dart tools must be re-activated.",
            (HOME / ".dartServer", HOME / ".pub-cache"),
        ),
        Category(
            "wallpapers",
            "Aerial wallpapers",
            "Downloaded Apple aerial video wallpapers.",
            (HOME / "Library/Application Support/com.apple.wallpaper/aerials",),
        ),
    ]
    if extra_paths:
        found.append(
            Category("custom", "Custom folders", "Folders listed in extra_paths of your config.", extra_paths)
        )
    if careful_paths:
        found.append(
            Category(
                "careful",
                "Custom careful folders",
                "Folders listed in careful_paths of your config. May contain unsynced work.",
                careful_paths,
                safe=False,
            )
        )
    excluded = set(config.get("exclude", ()))
    return [item for item in found if item.key not in excluded]


def clear_screen() -> None:
    print("\x1b[2J\x1b[H", end="")


def header(total: int, available: int) -> None:
    clear_screen()
    used = total - available
    print(color("SPACE SWEEP", PINK) + color("  macOS storage, made legible", DIM))
    print(color(RULE, DIM))
    print(f"{color(bytes_to_text(available), GREEN)} free of {bytes_to_text(total)}")
    print(f"{bar(used, total)}  {bytes_to_text(used)} used\n")


def disk_gauge(total: int, available: int) -> None:
    used = total - available
    percent = round(used / total * 100) if total else 0
    print(color("DISK PULSE", BOLD))
    print(color("       ╭──────────────╮", BLUE))
    print(
        color("      ╱", BLUE)
        + f"  {color(f'{percent}%', PINK)}  {color('USED', DIM)}  "
        + color("╲", BLUE)
    )
    print(color("     │", BLUE) + f"   {bytes_to_text(used):>8}   " + color("│", BLUE))
    print(
        color("      ╲", BLUE)
        + f" {color(bytes_to_text(available) + ' free', GREEN):^12} "
        + color("╱", BLUE)
    )
    print(color("       ╰──────────────╯", BLUE) + "\n")


def category_map(measured: list[tuple[Category, int]], width: int = 54) -> None:
    active = sorted(((item, size) for item, size in measured if size), key=lambda entry: entry[1], reverse=True)
    if not active:
        return
    shown = active[:6]
    remaining = sum(size for _, size in active[6:])
    parts = [
        (item.title, size, GRAPH_COLORS[index % len(GRAPH_COLORS)])
        for index, (item, size) in enumerate(shown)
    ]
    if remaining:
        parts.append(("Other", remaining, DIM))
    total = sum(size for _, size, _ in parts)
    segments = []
    used_width = 0
    for index, (_title, size, tone) in enumerate(parts):
        segment_width = (
            width - used_width
            if index == len(parts) - 1
            else max(1, round(size / total * width))
        )
        used_width += segment_width
        segments.append(color("█" * segment_width, tone))
    print(color("REMOVABLE SPACE MAP", BOLD))
    print("".join(segments))
    for title, size, tone in parts:
        print(f"{color('●', tone)} {title:<26} {bytes_to_text(size):>10}")
    print()


def render(items: list[Category]) -> tuple[dict[str, Category], dict[str, int]]:
    with ScanStatus():
        total, available = data_volume()
        measured = [(item, item.size()) for item in items]
    header(total, available)
    disk_gauge(total, available)
    category_map(measured)
    maximum = max((size for _, size in measured), default=1)
    print(color("REMOVABLE SPACE", BOLD))
    print(f"{'#':<4}{'CATEGORY':<29}{'SIZE':>10}   VISUAL")
    print(color(RULE, DIM))
    lookup = {}
    for index, (item, size) in enumerate(measured, 1):
        lookup[str(index)] = item
        label = item.title + ("" if item.safe else "  ⚠")
        print(f"{index:<4}{label:<29}{bytes_to_text(size):>10}   {bar(size, maximum)}")
    safe_total = sum(size for item, size in measured if item.safe)
    print(color(RULE, DIM))
    print("Safe one-touch cleanup: " + color(bytes_to_text(safe_total), YELLOW))
    print(color("Categories marked ⚠ are never included in one-touch cleanup.", DIM))
    return lookup, {item.key: size for item, size in measured}


def confirm(message: str, warning: bool = False) -> bool:
    tone = RED if warning else YELLOW
    answer = input(
        f"\n{message}\nPress {color('Enter', tone)} to continue, or type anything else to cancel: "
    )
    return answer.strip() == ""


def clean_category(item: Category) -> None:
    size = item.size()
    if not size:
        print(color("Nothing to clean in this category.", DIM))
        input("Press Enter to continue.")
        return
    if item.key != "simulators" and not confirm(
        f"Remove {bytes_to_text(size)} from {item.title}?\n{item.description}",
        warning=not item.safe,
    ):
        return
    removed, errors = cleaner_for(item)()
    print(color(f"Removed {len(removed)} item(s).", GREEN))
    for error in errors:
        print(color(f"• {error}", RED))
    input("Press Enter to continue.")


def one_touch(sizes: dict[str, int], items: list[Category]) -> None:
    safe = [item for item in items if item.safe and sizes[item.key]]
    amount = sum(sizes[item.key] for item in safe)
    if not confirm(
        f"One-touch cleanup will permanently remove {bytes_to_text(amount)} "
        f"across {len(safe)} safe categories."
    ):
        return
    errors: list[str] = []
    for item in safe:
        _removed, item_errors = cleaner_for(item, automatic=True)()
        errors.extend(item_errors)
    print(color("One-touch cleanup finished.", GREEN))
    if errors:
        print(
            color(
                f"{len(errors)} item(s) could not be removed (usually macOS-protected caches).",
                YELLOW,
            )
        )
    input("Press Enter to continue.")


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Space Sweep is designed for macOS.")
    try:
        while True:
            items = categories()
            lookup, sizes = render(items)
            print(f"\n[1–{len(items)}] clean a category   [a] one-touch clean   [r] rescan   [q] quit")
            choice = input(color("› ", PINK)).strip().lower()
            if choice == "q":
                break
            if choice == "a":
                one_touch(sizes, items)
            elif choice in lookup:
                clean_category(lookup[choice])
    except (EOFError, KeyboardInterrupt):
        print()
    print("\nDone. Stay spacious.\n")


if __name__ == "__main__":
    main()
