#!/usr/bin/env python3
"""
Neocities API TUI

A curses interface for the Neocities developer API:
  - list/browse remote files and directories
  - upload one or many local files
  - create remote directories
  - move/rename remote files or directories
  - delete remote files/directories
  - download remote files
  - show site information
  - compare local SHA-1 hashes with remote copies

API docs: https://neocities.org/api

Authentication:
  1. NEOCITIES_API_KEY from a .env file beside this script, or
  2. NEOCITIES_API_KEY already present in the process environment, or
  3. API key stored in ~/.config/neocities-tui/config.json (mode 0600)

The process environment takes precedence over .env, and .env takes precedence
over the config file.

The TUI never sends the key anywhere except neocities.org.
"""

from __future__ import annotations

import argparse
import curses
import getpass
import hashlib
import json
import os
import re
import stat
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

try:
    import requests
except ImportError:
    print("Missing dependency: requests", file=sys.stderr)
    print("Install it with: sudo apt install python3-requests", file=sys.stderr)
    raise SystemExit(2)


APP_NAME = "Neocities API TUI"
APP_VERSION = "1.0"
API_BASE = "https://neocities.org/api"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "neocities-tui"
CONFIG_FILE = CONFIG_DIR / "config.json"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
HTTP_TIMEOUT = 60

# A project-local .env file is supported without requiring python-dotenv.
# Put it beside this script, for example:
#   NEOCITIES_API_KEY=nc_...
DOTENV_FILE = Path(__file__).resolve().parent / ".env"
LOADED_DOTENV: Path | None = None


def load_dotenv_file(path: Path = DOTENV_FILE) -> Path | None:
    """Load simple KEY=VALUE entries from a private .env file.

    Existing process environment variables are never overwritten.  This parser
    intentionally supports the common .env forms needed here without adding a
    python-dotenv dependency: blank/comment lines, optional ``export``, and
    single/double-quoted values.
    """
    global LOADED_DOTENV
    if not path.exists():
        return None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            print(f"Warning: ignoring non-regular .env: {path}", file=sys.stderr)
            return None
        if info.st_uid != os.getuid():
            print(f"Warning: ignoring .env not owned by current user: {path}", file=sys.stderr)
            return None
        if stat.S_IMODE(info.st_mode) & 0o077:
            print(f"Warning: {path} is readable by other users; run: chmod 600 {path}", file=sys.stderr)

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
        LOADED_DOTENV = path
        return path
    except OSError as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
        return None


class APIError(RuntimeError):
    pass


class UserCancelled(Exception):
    pass


@dataclass
class RemoteEntry:
    path: str
    is_directory: bool
    size: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    sha1_hash: str | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name or self.path


@dataclass
class AppConfig:
    api_key: str = ""
    local_root: str = str(Path.cwd())
    download_dir: str = str(Path.home() / "Downloads" / "neocities")

    @classmethod
    def load(cls) -> "AppConfig":
        # Load a script-adjacent .env before checking NEOCITIES_API_KEY.
        # A key already exported in the shell still wins because the loader uses
        # os.environ.setdefault().
        load_dotenv_file()
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                info = CONFIG_FILE.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise RuntimeError(f"Config is not a regular file: {CONFIG_FILE}")
                if info.st_uid != os.getuid():
                    raise RuntimeError(f"Config is not owned by current user: {CONFIG_FILE}")
                if stat.S_IMODE(info.st_mode) & 0o077:
                    raise RuntimeError(f"Config permissions must be 0600: {CONFIG_FILE}")
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                cfg.api_key = str(data.get("api_key") or "")
                cfg.local_root = str(data.get("local_root") or cfg.local_root)
                cfg.download_dir = str(data.get("download_dir") or cfg.download_dir)
            except Exception as exc:
                print(f"Warning: could not load config: {exc}", file=sys.stderr)
        env_key = os.environ.get("NEOCITIES_API_KEY")
        if env_key:
            cfg.api_key = env_key.strip()
        return cfg

    def save(self, include_key: bool = True) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(CONFIG_DIR, 0o700)
        payload = {
            "local_root": str(Path(self.local_root).expanduser().resolve()),
            "download_dir": str(Path(self.download_dir).expanduser().resolve()),
        }
        if include_key:
            payload["api_key"] = self.api_key
        tmp = CONFIG_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, CONFIG_FILE)
        os.chmod(CONFIG_FILE, 0o600)


class NeocitiesAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"neocities-tui/{APP_VERSION}",
            "Accept": "application/json",
        })

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise APIError("No Neocities API key configured")
        return {"Authorization": f"Bearer {self.api_key}"}

    def _json(self, response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise APIError(f"HTTP {response.status_code}: non-JSON response")
        if response.status_code != 200 or data.get("result") != "success":
            message = data.get("message") or response.reason or "request failed"
            error_type = data.get("error_type")
            if error_type:
                message = f"{error_type}: {message}"
            raise APIError(f"HTTP {response.status_code}: {message}")
        return data

    def list(self, path: str | None = None) -> list[RemoteEntry]:
        params: dict[str, str] = {}
        if path is not None:
            params["path"] = path
        r = self.session.get(f"{API_BASE}/list", headers=self.headers, params=params, timeout=HTTP_TIMEOUT)
        data = self._json(r)
        result: list[RemoteEntry] = []
        for item in data.get("files", []):
            result.append(RemoteEntry(
                path=str(item.get("path") or ""),
                is_directory=bool(item.get("is_directory")),
                size=item.get("size"),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                sha1_hash=item.get("sha1_hash"),
            ))
        return result

    def list_children(self, directory: str) -> list[RemoteEntry]:
        directory = normalize_remote_dir(directory)
        if directory:
            return sorted(self.list(directory), key=remote_sort_key)

        # /api/list with no path is recursive. Reduce it to immediate root children.
        all_entries = self.list(None)
        children = [entry for entry in all_entries if "/" not in entry.path.strip("/")]
        return sorted(children, key=remote_sort_key)

    def upload(self, mapping: Sequence[tuple[str, Path]]) -> str:
        if not mapping:
            raise APIError("No files selected")
        total = 0
        opened: list[Any] = []
        files: list[tuple[str, tuple[str, Any, str]]] = []
        try:
            for remote_path, local_path in mapping:
                size = local_path.stat().st_size
                if size > MAX_UPLOAD_BYTES:
                    raise APIError(f"File exceeds 100 MB: {local_path}")
                total += size
                if total > MAX_UPLOAD_BYTES:
                    raise APIError("Combined upload exceeds the API's 100 MB request limit")
                fh = local_path.open("rb")
                opened.append(fh)
                # Requests accepts repeated multipart parts with arbitrary field names.
                files.append((remote_path, (local_path.name, fh, "application/octet-stream")))
            r = self.session.post(f"{API_BASE}/upload", headers=self.headers, files=files, timeout=HTTP_TIMEOUT)
            return str(self._json(r).get("message") or "Upload complete")
        finally:
            for fh in opened:
                try:
                    fh.close()
                except Exception:
                    pass

    def create_directory(self, path: str) -> str:
        r = self.session.post(
            f"{API_BASE}/create_directory",
            headers=self.headers,
            data={"path": normalize_remote_path(path)},
            timeout=HTTP_TIMEOUT,
        )
        return str(self._json(r).get("message") or "Directory created")

    def rename(self, path: str, new_path: str) -> str:
        r = self.session.post(
            f"{API_BASE}/rename",
            headers=self.headers,
            data={"path": normalize_remote_path(path), "new_path": normalize_remote_path(new_path)},
            timeout=HTTP_TIMEOUT,
        )
        return str(self._json(r).get("message") or "Rename complete")

    def delete(self, paths: Sequence[str]) -> str:
        if not paths:
            raise APIError("No paths selected")
        data = [("filenames[]", normalize_remote_path(path)) for path in paths]
        r = self.session.post(f"{API_BASE}/delete", headers=self.headers, data=data, timeout=HTTP_TIMEOUT)
        return str(self._json(r).get("message") or "Delete complete")

    def download(self, path: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(
            f"{API_BASE}/download",
            headers=self.headers,
            params={"path": normalize_remote_path(path)},
            timeout=HTTP_TIMEOUT,
            stream=True,
        ) as r:
            if r.status_code != 200:
                try:
                    data = r.json()
                    msg = data.get("message") or r.reason
                except ValueError:
                    msg = r.reason
                raise APIError(f"HTTP {r.status_code}: {msg}")
            tmp = destination.with_suffix(destination.suffix + ".part")
            with tmp.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        fh.write(chunk)
            os.replace(tmp, destination)
        return destination

    def info(self, sitename: str | None = None) -> dict[str, Any]:
        params = {"sitename": sitename} if sitename else None
        headers = self.headers if not sitename else {}
        r = self.session.get(f"{API_BASE}/info", headers=headers, params=params, timeout=HTTP_TIMEOUT)
        return dict(self._json(r).get("info") or {})

    def upload_hash(self, mapping: dict[str, str]) -> dict[str, bool]:
        if not mapping:
            raise APIError("No hashes to compare")
        # Direct flat fields: remote path -> sha1 value.
        r = self.session.post(f"{API_BASE}/upload_hash", headers=self.headers, data=mapping, timeout=HTTP_TIMEOUT)
        files = self._json(r).get("files") or {}
        return {str(k): bool(v) for k, v in files.items()}


# ---------- Path helpers ----------


def normalize_remote_path(path: str) -> str:
    path = str(path or "").replace("\\", "/").strip()
    path = path.lstrip("/")
    p = PurePosixPath(path)
    if not path or path in {".", "/"}:
        return ""
    if ".." in p.parts:
        raise ValueError("Remote path may not contain '..'")
    return str(p)


def normalize_remote_dir(path: str) -> str:
    return normalize_remote_path(path).rstrip("/")


def remote_join(directory: str, name: str) -> str:
    directory = normalize_remote_dir(directory)
    name = normalize_remote_path(name)
    return str(PurePosixPath(directory) / name) if directory else name


def remote_parent(path: str) -> str:
    path = normalize_remote_path(path)
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def remote_sort_key(entry: RemoteEntry) -> tuple[int, str]:
    return (0 if entry.is_directory else 1, entry.name.casefold())


def human_size(size: int | None) -> str:
    if size is None:
        return "-"
    value = float(size)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_local_root(path: str) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Local root is not a directory: {root}")
    return root


# ---------- Curses UI primitives ----------


def clipped(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    available = max(0, w - x - 1)
    if available <= 0:
        return
    try:
        stdscr.addnstr(y, x, str(text), available, attr)
    except curses.error:
        pass


def draw_header(stdscr: curses.window, title: str, subtitle: str = "") -> int:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    clipped(stdscr, 0, 0, f" {APP_NAME} v{APP_VERSION} ", curses.A_REVERSE | curses.A_BOLD)
    clipped(stdscr, 1, 1, title, curses.A_BOLD)
    if subtitle:
        clipped(stdscr, 2, 1, subtitle, curses.A_DIM)
        return 4
    return 3


def footer(stdscr: curses.window, text: str) -> None:
    h, _ = stdscr.getmaxyx()
    clipped(stdscr, h - 1, 0, " " + text, curses.A_REVERSE)


def wait_key(stdscr: curses.window, message: str = "Press any key") -> None:
    footer(stdscr, message)
    stdscr.refresh()
    stdscr.getch()


def message_box(stdscr: curses.window, title: str, lines: Sequence[str]) -> None:
    top = draw_header(stdscr, title)
    h, w = stdscr.getmaxyx()
    row = top
    for line in lines:
        wrapped = textwrap.wrap(str(line), width=max(20, w - 4)) or [""]
        for piece in wrapped:
            if row >= h - 2:
                wait_key(stdscr, "More - press any key")
                top = draw_header(stdscr, title)
                row = top
            clipped(stdscr, row, 2, piece)
            row += 1
    wait_key(stdscr)


def show_error(stdscr: curses.window, exc: Exception) -> None:
    message_box(stdscr, "Error", [str(exc)])


def prompt(stdscr: curses.window, label: str, initial: str = "", hidden: bool = False) -> str | None:
    h, w = stdscr.getmaxyx()
    row = h - 3
    try:
        stdscr.move(row, 0)
        stdscr.clrtoeol()
        clipped(stdscr, row, 1, label)
        x = min(w - 2, len(label) + 2)
        stdscr.move(row, x)
        curses.curs_set(1)
        if hidden:
            curses.noecho()
        else:
            curses.echo()
        if initial and not hidden:
            clipped(stdscr, row, x, initial)
            stdscr.move(row, min(w - 2, x + len(initial)))
            # For predictable editing, initial is a visual suggestion only.
        raw = stdscr.getstr(row, x, max(1, w - x - 2))
        value = raw.decode("utf-8", errors="replace")
        if not value and initial:
            value = initial
        return value
    except (KeyboardInterrupt, curses.error):
        return None
    finally:
        curses.noecho()
        curses.curs_set(0)


def confirm(stdscr: curses.window, question: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = prompt(stdscr, f"{question} {suffix}")
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"y", "yes"}


def menu(stdscr: curses.window, title: str, items: Sequence[tuple[str, str]], subtitle: str = "") -> str | None:
    index = 0
    while True:
        top = draw_header(stdscr, title, subtitle)
        h, _ = stdscr.getmaxyx()
        visible = max(1, h - top - 2)
        if index < 0:
            index = len(items) - 1
        if index >= len(items):
            index = 0
        start = max(0, min(index - visible + 1, len(items) - visible))
        for row, (key, label) in enumerate(items[start:start + visible], start=top):
            actual = start + (row - top)
            attr = curses.A_REVERSE if actual == index else 0
            clipped(stdscr, row, 2, f"{key:>2}  {label}", attr)
        footer(stdscr, "Up/Down navigate  Enter select  q/Esc back")
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return None
        if ch in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(items)
        elif ch in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(items)
        elif ch in (10, 13, curses.KEY_ENTER):
            return items[index][0]
        else:
            c = chr(ch).lower() if 0 <= ch < 256 else ""
            for key, _ in items:
                if c == key.lower():
                    return key


# ---------- Local browser ----------


def local_entries(root: Path, current: Path, filter_text: str = "") -> list[Path]:
    try:
        entries = [p for p in current.iterdir() if not p.name.startswith(".part")]
    except OSError:
        return []
    if filter_text:
        needle = filter_text.casefold()
        entries = [p for p in entries if needle in p.name.casefold()]
    return sorted(entries, key=lambda p: (0 if p.is_dir() else 1, p.name.casefold()))


def local_picker(
    stdscr: curses.window,
    root: Path,
    *,
    title: str,
    files_only: bool = True,
    multi: bool = False,
    choose_directory: bool = False,
    start: Path | None = None,
) -> list[Path] | Path | None:
    root = root.resolve()
    current = (start or root).resolve()
    try:
        current.relative_to(root)
    except ValueError:
        current = root
    if not current.exists() or not current.is_dir():
        current = root
    selected: set[Path] = set()
    index = 0
    offset = 0
    filter_text = ""

    while True:
        entries = local_entries(root, current, filter_text)
        top = draw_header(stdscr, title, f"Root: {root}    Current: {current}")
        h, _ = stdscr.getmaxyx()
        max_rows = max(1, h - top - 2)
        if entries:
            index = max(0, min(index, len(entries) - 1))
        else:
            index = 0
        if index < offset:
            offset = index
        elif index >= offset + max_rows:
            offset = index - max_rows + 1

        for row, entry in enumerate(entries[offset:offset + max_rows], start=top):
            actual = offset + row - top
            marker = "*" if entry.resolve() in selected else " "
            kind = "DIR " if entry.is_dir() else f"{human_size(entry.stat().st_size):>8}"
            text = f"[{marker}] {kind}  {entry.name}"
            attr = curses.A_REVERSE if actual == index else 0
            if entry.is_dir():
                attr |= curses.A_BOLD
            clipped(stdscr, row, 1, text, attr)

        controls = "Enter open/select  Backspace parent  / filter  c clear  q cancel"
        if multi:
            controls = "Space mark  Enter open  d done  Backspace parent  / filter  q cancel"
        if choose_directory:
            controls = "Enter open  d choose current dir  Backspace parent  / filter  q cancel"
        footer(stdscr, controls)
        stdscr.refresh()
        ch = stdscr.getch()

        if ch in (ord("q"), 27):
            return None
        if ch in (curses.KEY_UP, ord("k")) and entries:
            index = max(0, index - 1)
        elif ch in (curses.KEY_DOWN, ord("j")) and entries:
            index = min(len(entries) - 1, index + 1)
        elif ch == curses.KEY_PPAGE:
            index = max(0, index - max_rows)
        elif ch == curses.KEY_NPAGE:
            index = min(max(0, len(entries) - 1), index + max_rows)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if current != root:
                current = current.parent
                index = offset = 0
                filter_text = ""
        elif ch == ord("/"):
            value = prompt(stdscr, "Filter name contains:")
            if value is not None:
                filter_text = value.strip()
                index = offset = 0
        elif ch == ord("c"):
            filter_text = ""
            index = offset = 0
        elif choose_directory and ch == ord("d"):
            return current
        elif multi and ch == ord("d"):
            return sorted(selected)
        elif multi and ch == ord(" ") and entries:
            target = entries[index].resolve()
            if target.is_file() or not files_only:
                if target in selected:
                    selected.remove(target)
                else:
                    selected.add(target)
        elif ch in (10, 13, curses.KEY_ENTER) and entries:
            target = entries[index].resolve()
            if target.is_dir():
                current = target
                index = offset = 0
                filter_text = ""
            elif not choose_directory:
                if multi:
                    if target in selected:
                        selected.remove(target)
                    else:
                        selected.add(target)
                else:
                    return target


# ---------- Remote browser ----------


def remote_picker(
    stdscr: curses.window,
    api: NeocitiesAPI,
    *,
    title: str,
    files_only: bool = False,
    multi: bool = False,
    choose_directory: bool = False,
) -> list[RemoteEntry] | RemoteEntry | str | None:
    current = ""
    selected: dict[str, RemoteEntry] = {}
    index = 0
    offset = 0
    filter_text = ""

    while True:
        try:
            entries = api.list_children(current)
        except Exception as exc:
            show_error(stdscr, exc)
            return None
        if filter_text:
            needle = filter_text.casefold()
            entries = [e for e in entries if needle in e.name.casefold()]

        top = draw_header(stdscr, title, f"Remote: /{current}" if current else "Remote: /")
        h, _ = stdscr.getmaxyx()
        max_rows = max(1, h - top - 2)
        if entries:
            index = max(0, min(index, len(entries) - 1))
        else:
            index = 0
        if index < offset:
            offset = index
        elif index >= offset + max_rows:
            offset = index - max_rows + 1

        for row, entry in enumerate(entries[offset:offset + max_rows], start=top):
            actual = offset + row - top
            marker = "*" if entry.path in selected else " "
            kind = "DIR " if entry.is_directory else f"{human_size(entry.size):>8}"
            text = f"[{marker}] {kind}  {entry.name}"
            attr = curses.A_REVERSE if actual == index else 0
            if entry.is_directory:
                attr |= curses.A_BOLD
            clipped(stdscr, row, 1, text, attr)

        controls = "Enter open/select  Backspace parent  / filter  c clear  r refresh  q cancel"
        if multi:
            controls = "Space mark  Enter open  d done  Backspace parent  / filter  r refresh  q cancel"
        if choose_directory:
            controls = "Enter open  d choose current dir  Backspace parent  / filter  r refresh  q cancel"
        footer(stdscr, controls)
        stdscr.refresh()
        ch = stdscr.getch()

        if ch in (ord("q"), 27):
            return None
        if ch in (curses.KEY_UP, ord("k")) and entries:
            index = max(0, index - 1)
        elif ch in (curses.KEY_DOWN, ord("j")) and entries:
            index = min(len(entries) - 1, index + 1)
        elif ch == curses.KEY_PPAGE:
            index = max(0, index - max_rows)
        elif ch == curses.KEY_NPAGE:
            index = min(max(0, len(entries) - 1), index + max_rows)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if current:
                current = remote_parent(current)
                index = offset = 0
                filter_text = ""
        elif ch == ord("/"):
            value = prompt(stdscr, "Filter name contains:")
            if value is not None:
                filter_text = value.strip()
                index = offset = 0
        elif ch == ord("c"):
            filter_text = ""
            index = offset = 0
        elif ch == ord("r"):
            pass
        elif choose_directory and ch == ord("d"):
            return current
        elif multi and ch == ord("d"):
            return list(selected.values())
        elif multi and ch == ord(" ") and entries:
            target = entries[index]
            if target.path in selected:
                selected.pop(target.path, None)
            else:
                selected[target.path] = target
        elif ch in (10, 13, curses.KEY_ENTER) and entries:
            target = entries[index]
            if target.is_directory:
                current = target.path
                index = offset = 0
                filter_text = ""
            elif not choose_directory:
                if multi:
                    if target.path in selected:
                        selected.pop(target.path, None)
                    else:
                        selected[target.path] = target
                else:
                    return target


# ---------- Operations ----------


def op_browse_remote(stdscr: curses.window, api: NeocitiesAPI) -> None:
    current = ""
    index = 0
    offset = 0
    filter_text = ""
    while True:
        entries = api.list_children(current)
        if filter_text:
            needle = filter_text.casefold()
            entries = [e for e in entries if needle in e.name.casefold()]
        top = draw_header(stdscr, "Remote browser", f"Remote: /{current}" if current else "Remote: /")
        h, _ = stdscr.getmaxyx()
        rows = max(1, h - top - 2)
        if entries:
            index = max(0, min(index, len(entries) - 1))
        else:
            index = 0
        if index < offset:
            offset = index
        elif index >= offset + rows:
            offset = index - rows + 1

        for row, entry in enumerate(entries[offset:offset + rows], start=top):
            actual = offset + row - top
            stamp = (entry.updated_at or "")[:25]
            size = "<DIR>" if entry.is_directory else human_size(entry.size)
            text = f"{size:>10}  {stamp:<25}  {entry.name}"
            attr = curses.A_REVERSE if actual == index else 0
            if entry.is_directory:
                attr |= curses.A_BOLD
            clipped(stdscr, row, 1, text, attr)
        footer(stdscr, "Enter open/details  Backspace parent  / filter  c clear  r refresh  q back")
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        if ch in (curses.KEY_UP, ord("k")) and entries:
            index = max(0, index - 1)
        elif ch in (curses.KEY_DOWN, ord("j")) and entries:
            index = min(len(entries) - 1, index + 1)
        elif ch == curses.KEY_PPAGE:
            index = max(0, index - rows)
        elif ch == curses.KEY_NPAGE:
            index = min(max(0, len(entries) - 1), index + rows)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if current:
                current = remote_parent(current)
                index = offset = 0
                filter_text = ""
        elif ch == ord("/"):
            value = prompt(stdscr, "Filter name contains:")
            if value is not None:
                filter_text = value.strip()
                index = offset = 0
        elif ch == ord("c"):
            filter_text = ""
            index = offset = 0
        elif ch == ord("r"):
            pass
        elif ch in (10, 13, curses.KEY_ENTER) and entries:
            target = entries[index]
            if target.is_directory:
                current = target.path
                index = offset = 0
                filter_text = ""
            else:
                message_box(stdscr, "Remote file", [
                    f"Path: /{target.path}",
                    f"Size: {human_size(target.size)}",
                    f"Created: {target.created_at or '-'}",
                    f"Updated: {target.updated_at or '-'}",
                    f"SHA-1: {target.sha1_hash or '-'}",
                ])


def op_upload(stdscr: curses.window, api: NeocitiesAPI, cfg: AppConfig) -> None:
    root = safe_local_root(cfg.local_root)
    picked = local_picker(stdscr, root, title="Select local files to upload", files_only=True, multi=True)
    if not picked:
        return
    assert isinstance(picked, list)
    remote_dir = remote_picker(stdscr, api, title="Choose remote destination directory", choose_directory=True)
    if remote_dir is None:
        return
    assert isinstance(remote_dir, str)

    mapping: list[tuple[str, Path]] = []
    for local in picked:
        rel = local.resolve().relative_to(root)
        destination = remote_join(remote_dir, rel.as_posix())
        mapping.append((destination, local))

    total = sum(path.stat().st_size for _, path in mapping)
    preview = [f"{local} -> /{remote}" for remote, local in mapping[:12]]
    if len(mapping) > 12:
        preview.append(f"... and {len(mapping)-12} more")
    preview.append(f"Total: {human_size(total)}")
    preview.append("Missing destination directories are created automatically by the API.")
    message_box(stdscr, "Upload plan", preview)
    if not confirm(stdscr, f"Upload {len(mapping)} file(s)?"):
        return
    msg = api.upload(mapping)
    message_box(stdscr, "Upload", [msg])


def op_download(stdscr: curses.window, api: NeocitiesAPI, cfg: AppConfig) -> None:
    picked = remote_picker(stdscr, api, title="Select remote file to download", files_only=True)
    if not isinstance(picked, RemoteEntry) or picked.is_directory:
        return
    root = Path(cfg.download_dir).expanduser().resolve()
    destination = root / PurePosixPath(picked.path)
    if destination.exists() and not confirm(stdscr, f"Overwrite {destination}?"):
        return
    saved = api.download(picked.path, destination)
    message_box(stdscr, "Download complete", [str(saved)])


def op_create_directory(stdscr: curses.window, api: NeocitiesAPI) -> None:
    parent = remote_picker(stdscr, api, title="Choose parent directory", choose_directory=True)
    if parent is None:
        return
    assert isinstance(parent, str)
    name = prompt(stdscr, "New directory name:")
    if not name or not name.strip():
        return
    name = name.strip().strip("/")
    if "/" in name or "\\" in name:
        raise ValueError("Enter one directory name; choose the parent with the browser")
    target = remote_join(parent, name)
    if not confirm(stdscr, f"Create /{target}?"):
        return
    message_box(stdscr, "Create directory", [api.create_directory(target)])


def op_rename(stdscr: curses.window, api: NeocitiesAPI) -> None:
    picked = remote_picker(stdscr, api, title="Select file or directory to move/rename")
    if not isinstance(picked, RemoteEntry):
        return
    destination_dir = remote_picker(stdscr, api, title="Choose destination parent directory", choose_directory=True)
    if destination_dir is None:
        return
    assert isinstance(destination_dir, str)
    new_name = prompt(stdscr, "New name:", initial=picked.name)
    if not new_name or not new_name.strip():
        return
    new_name = new_name.strip().strip("/")
    if "/" in new_name or "\\" in new_name:
        raise ValueError("Enter only the new name; choose the destination directory with the browser")
    new_path = remote_join(destination_dir, new_name)
    if not confirm(stdscr, f"Move /{picked.path} -> /{new_path}?"):
        return
    message_box(stdscr, "Rename/move", [api.rename(picked.path, new_path)])


def op_delete(stdscr: curses.window, api: NeocitiesAPI) -> None:
    picked = remote_picker(stdscr, api, title="Select remote paths to DELETE", multi=True)
    if not picked:
        return
    assert isinstance(picked, list)
    paths = [entry.path for entry in picked]
    lines = [f"/{path}" for path in paths[:20]]
    if len(paths) > 20:
        lines.append(f"... and {len(paths)-20} more")
    lines.append("")
    lines.append("WARNING: deleting a directory deletes everything inside it. This cannot be undone.")
    message_box(stdscr, "Delete plan", lines)
    typed = prompt(stdscr, f"Type DELETE to remove {len(paths)} path(s):")
    if typed != "DELETE":
        return
    message_box(stdscr, "Delete", [api.delete(paths)])


def op_hash_compare(stdscr: curses.window, api: NeocitiesAPI, cfg: AppConfig) -> None:
    root = safe_local_root(cfg.local_root)
    picked = local_picker(stdscr, root, title="Select local files for hash comparison", files_only=True, multi=True)
    if not picked:
        return
    assert isinstance(picked, list)
    remote_dir = remote_picker(stdscr, api, title="Choose corresponding remote base directory", choose_directory=True)
    if remote_dir is None:
        return
    assert isinstance(remote_dir, str)

    hash_map: dict[str, str] = {}
    source_by_remote: dict[str, Path] = {}
    top = draw_header(stdscr, "Computing SHA-1 hashes")
    stdscr.refresh()
    for i, local in enumerate(picked, 1):
        rel = local.resolve().relative_to(root)
        remote = remote_join(remote_dir, rel.as_posix())
        clipped(stdscr, top, 2, f"{i}/{len(picked)}  {local.name}")
        stdscr.refresh()
        hash_map[remote] = sha1_file(local)
        source_by_remote[remote] = local

    result = api.upload_hash(hash_map)
    changed = [remote for remote, same in result.items() if not same]
    same = [remote for remote, same in result.items() if same]
    lines = [f"Up to date: {len(same)}", f"Needs upload: {len(changed)}", ""]
    lines.extend(f"CHANGED  /{path}" for path in changed[:30])
    if len(changed) > 30:
        lines.append(f"... and {len(changed)-30} more changed file(s)")
    message_box(stdscr, "Hash comparison", lines)
    if changed and confirm(stdscr, f"Upload the {len(changed)} changed file(s) now?"):
        mapping = [(remote, source_by_remote[remote]) for remote in changed]
        message_box(stdscr, "Upload changed files", [api.upload(mapping)])


def op_site_info(stdscr: curses.window, api: NeocitiesAPI) -> None:
    choice = menu(stdscr, "Site information", [
        ("1", "My authenticated site"),
        ("2", "Public information for another sitename"),
    ])
    if choice is None:
        return
    sitename = None
    if choice == "2":
        value = prompt(stdscr, "Neocities sitename:")
        if not value or not value.strip():
            return
        sitename = value.strip()
    info = api.info(sitename)
    lines = [
        f"Site: {info.get('sitename', '-')}",
        f"Domain: {info.get('domain') or '-'}",
        f"Supporter: {info.get('supporter', False)}",
        f"Views: {info.get('views', '-')}",
        f"Hits: {info.get('hits', '-')}",
        f"Created: {info.get('created_at', '-')}",
        f"Last updated: {info.get('last_updated', '-')}",
        f"Tags: {', '.join(info.get('tags') or []) or '-'}",
    ]
    message_box(stdscr, "Site information", lines)


def op_settings(stdscr: curses.window, cfg: AppConfig) -> bool:
    changed_key = False
    while True:
        if os.environ.get("NEOCITIES_API_KEY"):
            key_state = f"loaded from {LOADED_DOTENV}" if LOADED_DOTENV else "set via environment"
        else:
            key_state = "stored" if cfg.api_key else "not set"
        choice = menu(stdscr, "Settings", [
            ("1", f"API key ({key_state})"),
            ("2", f"Local root: {cfg.local_root}"),
            ("3", f"Download directory: {cfg.download_dir}"),
            ("4", "Save settings"),
        ])
        if choice is None:
            return changed_key
        if choice == "1":
            if os.environ.get("NEOCITIES_API_KEY"):
                if LOADED_DOTENV:
                    message = f"NEOCITIES_API_KEY was loaded from {LOADED_DOTENV}. It takes precedence over the config file."
                else:
                    message = "NEOCITIES_API_KEY is set in the process environment and takes precedence over the config file."
                message_box(stdscr, "API key", [message])
            else:
                value = prompt(stdscr, "Paste Neocities API key:", hidden=True)
                if value is not None and value.strip():
                    cfg.api_key = value.strip()
                    changed_key = True
                    if confirm(stdscr, "Save API key to config file with mode 0600?"):
                        cfg.save(include_key=True)
        elif choice == "2":
            start = Path(cfg.local_root).expanduser()
            if not start.exists() or not start.is_dir():
                start = Path.cwd()
            selected = local_picker(
                stdscr,
                Path("/"),
                title="Choose local root",
                choose_directory=True,
                start=start.resolve(),
            )
            if isinstance(selected, Path):
                cfg.local_root = str(selected)
        elif choice == "3":
            value = prompt(stdscr, "Download directory:", initial=cfg.download_dir)
            if value:
                cfg.download_dir = str(Path(value).expanduser().resolve())
        elif choice == "4":
            cfg.save(include_key=not bool(os.environ.get("NEOCITIES_API_KEY")))
            message_box(stdscr, "Settings", [f"Saved to {CONFIG_FILE}"])


# ---------- Main application ----------


def ensure_key_before_curses(cfg: AppConfig) -> None:
    if cfg.api_key:
        return
    print("No Neocities API key is configured.")
    print("Get one from your Neocities site Settings -> API key.")
    key = getpass.getpass("API key (input hidden): ").strip()
    if not key:
        raise SystemExit("No API key provided")
    cfg.api_key = key
    answer = input(f"Save it to {CONFIG_FILE} with mode 0600? [y/N] ").strip().lower()
    if answer in {"y", "yes"}:
        cfg.save(include_key=True)


def run_tui(stdscr: curses.window, cfg: AppConfig) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    try:
        curses.use_default_colors()
    except curses.error:
        pass

    api = NeocitiesAPI(cfg.api_key)
    while True:
        subtitle = f"Local root: {cfg.local_root}"
        choice = menu(stdscr, "Main menu", [
            ("1", "Browse remote site"),
            ("2", "Upload local files"),
            ("3", "Download remote file"),
            ("4", "Create remote directory"),
            ("5", "Rename / move remote path"),
            ("6", "Delete remote files/directories"),
            ("7", "Compare local files with remote hashes"),
            ("8", "Site information"),
            ("9", "Settings"),
        ], subtitle=subtitle)
        if choice is None:
            return
        try:
            if choice == "1":
                op_browse_remote(stdscr, api)
            elif choice == "2":
                op_upload(stdscr, api, cfg)
            elif choice == "3":
                op_download(stdscr, api, cfg)
            elif choice == "4":
                op_create_directory(stdscr, api)
            elif choice == "5":
                op_rename(stdscr, api)
            elif choice == "6":
                op_delete(stdscr, api)
            elif choice == "7":
                op_hash_compare(stdscr, api, cfg)
            elif choice == "8":
                op_site_info(stdscr, api)
            elif choice == "9":
                key_changed = op_settings(stdscr, cfg)
                if key_changed:
                    api = NeocitiesAPI(cfg.api_key)
        except Exception as exc:
            show_error(stdscr, exc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Curses TUI for the Neocities developer API")
    parser.add_argument("--local-root", help="Override starting local file root")
    parser.add_argument("--download-dir", help="Override download destination")
    parser.add_argument("--no-save", action="store_true", help="Do not write changed path settings on exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = AppConfig.load()
    if args.local_root:
        cfg.local_root = str(Path(args.local_root).expanduser().resolve())
    if args.download_dir:
        cfg.download_dir = str(Path(args.download_dir).expanduser().resolve())
    try:
        safe_local_root(cfg.local_root)
    except ValueError:
        cfg.local_root = str(Path.cwd())
    ensure_key_before_curses(cfg)
    try:
        curses.wrapper(run_tui, cfg)
    except KeyboardInterrupt:
        pass
    finally:
        if not args.no_save:
            try:
                cfg.save(include_key=not bool(os.environ.get("NEOCITIES_API_KEY")))
            except Exception as exc:
                print(f"Warning: could not save settings: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
