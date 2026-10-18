from __future__ import annotations

import random
import sys
import ctypes
import platform
import os
import json
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Callable


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
RESTART_STATE_NAME = "restart-state.json"
INITIAL_DELAY = 10.00
DELAY_STEP = 0.09
MIN_DELAY = 0.50
VIDEO_INTERVAL = (120, 300)
VIDEO_SIZE = (640, 480)
AUDIO_INTERVAL = (10, 30)
WEBSITE_REDIRECT_DELAY = 180
TIMEOUT_WEBSITE = "https://yellow-union-fbb7.heheitzoffcamber.workers.dev/"
STARTUP_WEBSITES = (
    "https://turnmeintoagirl.com",
    "https://puginarug.com",
    "https://longdogechallenge.com",
    "https://hooooooooo.com",
    "https://cat-bounce.com",
    "https://endless.horse",
)


def _set_process_name() -> None:
    if platform.system() == "Linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(15, ctypes.c_char_p(b"aneemay"), 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, "Could not set Linux process name")


def _image_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "images"


def _video_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "media" / "rickroll.mp4"


def _audio_paths() -> list[Path]:
    media_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "media"
    if not media_dir.is_dir():
        return []
    return sorted(
        path for path in media_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp3"
    )


def _restart_state_path() -> Path:
    if platform.system() == "Windows":
        state_home = Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
        return state_home / "Aneemay" / RESTART_STATE_NAME
    state_home = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    )
    return state_home / "aneemay" / RESTART_STATE_NAME


def _load_restart_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("delay"), (int, float))
        or not isinstance(state.get("images_shown"), int)
        or not isinstance(state.get("next_image_at"), (int, float))
        or state["delay"] < 0
        or state["images_shown"] < 0
    ):
        return None
    return state


def _save_restart_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(state), encoding="utf-8")
    temporary_path.replace(path)


def _open_random_website(rng=None) -> str:
    website = (rng or random).choice(STARTUP_WEBSITES)
    if not webbrowser.open(website, new=2):
        raise RuntimeError("Could not open a website in the default browser.")
    return website


def random_position(screen_size: tuple[int, int], window_size: tuple[int, int], rng) -> tuple[int, int]:
    max_x = max(0, screen_size[0] - window_size[0])
    max_y = max(0, screen_size[1] - window_size[1])
    return rng.randint(0, max_x), rng.randint(0, max_y)


class ImageDisplayError(Exception):
    pass


def prepare_image(path: Path, maximum_size: tuple[int, int]):
    from PIL import Image

    with Image.open(path) as source:
        source.load()
        image = source.copy()
    scale = min(maximum_size[0] / image.width, maximum_size[1] / image.height, 8)
    if scale < 1:
        image.thumbnail(maximum_size, Image.Resampling.LANCZOS)
    elif scale > 1:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


class SlideshowController:
    def __init__(
        self,
        root,
        image_dir: Path,
        show_image: Callable[[Path, Callable[[], None]], None],
        close_image: Callable[[], None],
        on_status: Callable[[str], None],
        on_stats: Callable[[str], None],
        rng=None,
        video_path: Path | None = None,
        play_video: Callable[[Path], subprocess.Popen] | None = None,
        audio_paths: list[Path] | tuple[Path, ...] | None = None,
        play_audio: Callable[[Path], subprocess.Popen] | None = None,
        delay: float = INITIAL_DELAY,
        images_shown: int = 0,
        next_image_at: float | None = None,
    ):
        self.root = root
        self.image_dir = Path(image_dir)
        self.show_image = show_image
        self.close_image = close_image
        self.on_status = on_status
        self.on_stats = on_stats
        self.rng = rng or random.Random()
        self.video_path = Path(video_path) if video_path else None
        self.play_video = play_video
        self.audio_paths = [Path(path) for path in audio_paths] if audio_paths else []
        self.play_audio = play_audio
        self.video_timer_id = None
        self.video_poll_id = None
        self.video_processes: list[subprocess.Popen] = []
        self.audio_timer_id = None
        self.audio_poll_id = None
        self.audio_processes: list[subprocess.Popen] = []
        self.delay = delay
        self.images_shown = images_shown
        self.open_images = 0
        self.timer_id = None
        self.next_image_at = next_image_at
        self.stopped = False
        self._remaining_images: list[Path] = []

    def _f(self) -> list[Path]:
        if not self.image_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def start(self) -> bool:
        if self.stopped:
            return False
        if not self.image_dir.is_dir():
            self.on_status(f"Image folder not found: {self.image_dir}")
            return False
        if not self._f():
            self.on_status(f"No JPG, PNG, or WebP images found in: {self.image_dir}")
            return False
        self._u()
        self.on_status("Waiting for the first image.")
        self._schedule_next()
        self._schedule_video()
        self._schedule_audio()
        return True

    def _u(self) -> None:
        self.on_stats(
            f"Delay: {self.delay:.2f} seconds | Images shown: {self.images_shown} "
            f"| Open: {self.open_images}"
        )

    def _schedule_next(self) -> None:
        if not self.stopped and self.timer_id is None:
            milliseconds = (
                max(0, round((self.next_image_at - time.time()) * 1000))
                if self.next_image_at is not None
                else round(self.delay * 1000)
            )
            self.next_image_at = time.time() + milliseconds / 1000
            self.timer_id = self.root.after(milliseconds, self._open_next_image)

    def _reschedule_next(self) -> None:
        if self.timer_id is not None:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.next_image_at = None
        self._schedule_next()

    def _schedule_video(self) -> None:
        if (
            not self.stopped
            and self.video_path is not None
            and self.video_path.is_file()
            and self.play_video is not None
            and self.video_timer_id is None
        ):
            seconds = self.rng.randint(*VIDEO_INTERVAL)
            self.video_timer_id = self.root.after(seconds * 1000, self._open_video)

    def _open_video(self) -> None:
        self.video_timer_id = None
        if self.stopped:
            return
        if self.play_video is None or self.video_path is None or not self.video_path.is_file():
            return
        try:
            self.video_processes.append(self.play_video(self.video_path))
            self.on_status('rickrolled')
            self._poll_videos()
        except (OSError, RuntimeError) as error:
            self.on_status(f"Could not play video: {error}")
        self._schedule_video()

    def _poll_videos(self) -> None:
        self.video_poll_id = None
        if self.stopped:
            return
        self.video_processes = [process for process in self.video_processes if process.poll() is None]
        if self.video_processes:
            self.video_poll_id = self.root.after(500, self._poll_videos)

    def _schedule_audio(self) -> None:
        if (
            not self.stopped
            and any(path.is_file() for path in self.audio_paths)
            and self.play_audio is not None
            and self.audio_timer_id is None
        ):
            seconds = self.rng.randint(*AUDIO_INTERVAL)
            self.audio_timer_id = self.root.after(seconds * 1000, self._open_audio)

    def _open_audio(self) -> None:
        self.audio_timer_id = None
        if self.stopped:
            return
        available_paths = [path for path in self.audio_paths if path.is_file()]
        if self.play_audio is None or not available_paths:
            return
        try:
            audio_path = available_paths[self.rng.randint(0, len(available_paths) - 1)]
            self.audio_processes.append(self.play_audio(audio_path))
            self._poll_audio()
        except (OSError, RuntimeError) as error:
            self.on_status(f"Could not play sound: {error}")
        self._schedule_audio()

    def _poll_audio(self) -> None:
        self.audio_poll_id = None
        if self.stopped:
            return
        self.audio_processes = [process for process in self.audio_processes if process.poll() is None]
        if self.audio_processes:
            self.audio_poll_id = self.root.after(500, self._poll_audio)

    def _next_candidate(self) -> Path | None:
        if not self._remaining_images:
            self._remaining_images = self._f()
            self.rng.shuffle(self._remaining_images)
        if not self._remaining_images:
            return None
        return self._remaining_images.pop()

    def _open_next_image(self) -> None:
        self.timer_id = None
        self.next_image_at = None
        if self.stopped:
            return

        if not self._remaining_images:
            self._remaining_images = self._f()
            self.rng.shuffle(self._remaining_images)
        attempts_left = len(self._remaining_images)
        while not self.stopped and attempts_left:
            attempts_left -= 1
            path = self._next_candidate()
            if path is None:
                break
            try:
                self.open_images += 1
                self.show_image(path, self.image_closed)
                self._u()
                self.on_status(f"Displaying {path.name}.")
                self._schedule_next()
                return
            except ImageDisplayError as error:
                self.open_images -= 1
                print(f"Could not display {path.name}: {error}", file=sys.stderr)
                self.on_status(f"Could not display {path.name}: {error}")
                self._schedule_next()
                return
            except Exception as error:
                self.open_images -= 1
                self.on_status(f"Could not open {path.name}: {error}. Skipping this file.")
        if not self.stopped:
            self.on_status(
                f"No readable images found in: {self.image_dir}. Check the image files and formats."
            )

    def image_closed(self) -> None:
        if self.stopped:
            return
        if self.open_images <= 0:
            return
        self.open_images -= 1
        self.images_shown += 1
        # Reduce the delay after each close and retime the next nonblocking Tk callback.
        self.delay = max(MIN_DELAY, round(self.delay - DELAY_STEP, 2))
        self._u()
        self.on_status(f"Images open: {self.open_images}")
        self._reschedule_next()

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        # Cancel the pending callback and destroy the owned image window before Tk exits.
        if self.timer_id is not None:
            try:
                self.root.after_cancel(self.timer_id)
            except Exception:
                # The callback may already have left Tk's event queue.
                pass
            self.timer_id = None
        for timer_id in (
            self.video_timer_id,
            self.video_poll_id,
            self.audio_timer_id,
            self.audio_poll_id,
        ):
            if timer_id is not None:
                try:
                    self.root.after_cancel(timer_id)
                except Exception:
                    pass
        self.video_timer_id = None
        self.video_poll_id = None
        self.audio_timer_id = None
        self.audio_poll_id = None
        if self.open_images:
            self.open_images = 0
            self.close_image()
        for process in self.video_processes + self.audio_processes:
            if process.poll() is None:
                process.terminate()
        for process in self.video_processes + self.audio_processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.video_processes.clear()
        self.audio_processes.clear()


class SlideshowApp:
    def __init__(
        self,
        root,
        tk,
        ttk,
        image_dir: Path,
        video_path: Path | None = None,
        audio_paths: list[Path] | tuple[Path, ...] | None = None,
    ):
        self.root = root
        self.tk = tk
        self.ttk = ttk
        self._windows = {}
        self._s = tk.StringVar(value="Starting slideshow…")
        self._n = tk.StringVar(value="Delay: 10.00 seconds | Images shown: 0 | Open: 0")
        self._i = None
        self.restart_state_path = _restart_state_path()
        self._website_opened = False
        self._timeout_website_opened = False
        self._website_redirect_timer_id = None
        self._code_entered = False
        try:
            restart_state = _load_restart_state(self.restart_state_path)
        except (OSError, json.JSONDecodeError) as error:
            restart_state = None
            self._s.set(f"Could not read saved cooldown: {error}")
        if restart_state:
            self._website_opened = restart_state.get("website_opened", False)
            self._timeout_website_opened = restart_state.get(
                "timeout_website_opened", False
            )
            self._code_entered = restart_state.get("code_entered", False)
            saved_redirect_at = restart_state.get("website_redirect_at")
        else:
            saved_redirect_at = None
        self._website_redirect_at = (
            saved_redirect_at
            if isinstance(saved_redirect_at, (int, float))
            else time.time() + WEBSITE_REDIRECT_DELAY
        )

        root.title("aneemay")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="aneemay", font=("TkDefaultFont", 14, "bold")).pack(
            anchor="w"
        )
        ttk.Label(frame, textvariable=self._n).pack(anchor="w", pady=(10, 0))
        ttk.Label(frame, textvariable=self._s, wraplength=420).pack(
            anchor="w", pady=(10, 14)
        )
        ttk.Label(
            frame,
            text="Closing this window restarts aneemay. Solve the riddle to exit: "
            "“I bring everything to a halt.”",
            wraplength=420,
        ).pack(anchor="w")
        command_row = ttk.Frame(frame)
        command_row.pack(fill="x", pady=(5, 12))
        self._i = ttk.Entry(command_row)
        self._i.pack(side="left", fill="x", expand=True)
        self._i.bind("<Return>", self._q)
        ttk.Button(command_row, text="Submit", command=self._q).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(frame, text="❤️ with love from slappin ❤️").pack(
            anchor="center", pady=(4, 0)
        )

        self.controller = SlideshowController(
            root,
            image_dir,
            self._v,
            self._x,
            self._s.set,
            self._n.set,
            video_path=video_path,
            play_video=self._play_video,
            audio_paths=audio_paths,
            play_audio=self._play_audio,
            delay=restart_state["delay"] if restart_state else INITIAL_DELAY,
            images_shown=restart_state["images_shown"] if restart_state else 0,
            next_image_at=restart_state["next_image_at"] if restart_state else None,
        )
        try:
            import PIL  # noqa: F401
        except ImportError:
            self._s.set("Pillow is missing. Install it with: python3 -m pip install -r requirements.txt")
            return
        started = self.controller.start()
        if started and not self._website_opened:
            try:
                website = _open_random_website(self.controller.rng)
                self._s.set(f"Opened website: {website}")
                self._website_opened = True
            except (OSError, RuntimeError) as error:
                self._s.set(f"Could not open startup website: {error}")
        if started:
            self._schedule_timeout_website()

    def _q(self, _event=None) -> None:
        answer = self._i.get()
        if answer == "stop":
            self._mark_code_entered()
            self.stop()
        else:
            self._s.set("That is not the right answer. Try another word.")
            self._i.delete(0, "end")

    def _mark_code_entered(self) -> None:
        if self._code_entered:
            return
        self._code_entered = True
        if self._website_redirect_timer_id is not None:
            self.root.after_cancel(self._website_redirect_timer_id)
            self._website_redirect_timer_id = None

    def _schedule_timeout_website(self) -> None:
        if (
            self.controller.stopped
            or self._code_entered
            or self._timeout_website_opened
            or self._website_redirect_timer_id is not None
        ):
            return
        remaining_ms = max(
            0, round((self._website_redirect_at - time.time()) * 1000)
        )
        self._website_redirect_timer_id = self.root.after(
            remaining_ms, self._open_timeout_website
        )

    def _open_timeout_website(self) -> None:
        self._website_redirect_timer_id = None
        if self.controller.stopped or self._code_entered or self._timeout_website_opened:
            return
        self._timeout_website_opened = True
        try:
            if not webbrowser.open(TIMEOUT_WEBSITE, new=2):
                raise RuntimeError("Could not open the website in the default browser.")
            self._s.set(f"Opened website: {TIMEOUT_WEBSITE}")
        except (OSError, RuntimeError) as error:
            self._s.set(f"Could not open timeout website: {error}")

    def _on_close(self) -> None:
        if self.controller.stopped:
            self.root.destroy()
            return
        executable = Path(sys.executable).resolve()
        if getattr(sys, "frozen", False):
            command = [str(executable), *sys.argv[1:]]
            working_directory = executable.parent
        else:
            script = Path(sys.argv[0]).resolve()
            command = [str(executable), str(script), *sys.argv[1:]]
            working_directory = script.parent
        try:
            _save_restart_state(
                self.restart_state_path,
                {
                    "delay": self.controller.delay,
                    "images_shown": self.controller.images_shown,
                    "next_image_at": self.controller.next_image_at or time.time(),
                    "website_opened": True,
                    "timeout_website_opened": self._timeout_website_opened,
                    "code_entered": self._code_entered,
                    "website_redirect_at": self._website_redirect_at,
                },
            )
        except OSError as error:
            self._s.set(f"Could not save cooldown before restarting: {error}")
            return
        self.controller.stop()
        try:
            os.execv(str(executable), command)
        except OSError as exec_error:
            spawn_options = {
                "cwd": working_directory,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                spawn_options["creationflags"] = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            else:
                spawn_options["start_new_session"] = True
            try:
                subprocess.Popen(command, **spawn_options)
            except OSError as spawn_error:
                self.controller.stopped = False
                self.controller.start()
                self._schedule_timeout_website()
                self._s.set(
                    f"Could not restart aneemay: {exec_error}; fallback failed: {spawn_error}"
                )
                return
        self.root.destroy()

    def _v(self, path: Path, on_close: Callable[[], None]) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        image = prepare_image(path, (max(1, screen_width - 100), max(1, screen_height - 140)))
        from PIL import ImageTk

        window = None
        try:
            photo = ImageTk.PhotoImage(image, master=self.root)
            window = self.tk.Toplevel(self.root)
            window.title(path.name)
            window.protocol("WM_DELETE_WINDOW", lambda: self._y(window, on_close))
            label = self.ttk.Label(window, image=photo)
            label.pack()
            self._windows[window] = photo
            x, y = random_position(
                (self.root.winfo_screenwidth(), self.root.winfo_screenheight()),
                (image.width, image.height),
                self.controller.rng,
            )
            window.geometry(f"{image.width}x{image.height}+{x}+{y}")
            window.lift()
        except Exception as error:
            if window is not None:
                window.destroy()
            raise ImageDisplayError(f"Tk could not create the image window: {error}") from error

    def _play_video(self, path: Path) -> subprocess.Popen:
        player = shutil.which("ffplay")
        if player is None:
            if platform.system() == "Windows":
                message = "ffplay.exe is not installed or not on PATH. Install FFmpeg and add its bin folder to PATH."
            else:
                message = "ffplay is not installed. Install it with: sudo apt install ffmpeg"
            raise RuntimeError(message)
        width, height = VIDEO_SIZE
        x, y = random_position(
            (self.root.winfo_screenwidth(), self.root.winfo_screenheight()),
            VIDEO_SIZE,
            self.controller.rng,
        )
        environment = os.environ.copy()
        environment["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"
        return subprocess.Popen(
            [
                player,
                "-loglevel", "error",
                "-window_title", "rickrolled",
                "-x", str(width),
                "-y", str(height),
                "-autoexit",
                str(path),
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _play_audio(self, path: Path) -> subprocess.Popen:
        player = shutil.which("ffplay")
        if player is None:
            if platform.system() == "Windows":
                message = "ffplay.exe is not installed or not on PATH. Install FFmpeg and add its bin folder to PATH."
            else:
                message = "ffplay is not installed. Install it with: sudo apt install ffmpeg"
            raise RuntimeError(message)
        return subprocess.Popen(
            [
                player,
                "-loglevel", "error",
                "-nodisp",
                "-autoexit",
                str(path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _y(self, window, on_close: Callable[[], None]) -> None:
        if window not in self._windows:
            return
        del self._windows[window]
        window.destroy()
        on_close()

    def _x(self) -> None:
        for window in tuple(self._windows):
            del self._windows[window]
            window.destroy()

    def stop(self) -> None:
        if self._website_redirect_timer_id is not None:
            self.root.after_cancel(self._website_redirect_timer_id)
            self._website_redirect_timer_id = None
        self.restart_state_path.unlink(missing_ok=True)
        self.controller.stop()
        self.root.destroy()


def main() -> int:
    _set_process_name()
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print(
            "Tkinter is not installed. On Debian/Kali/Ubuntu, install it with "
            "'sudo apt install python3-tk'. On Windows, reinstall Python and enable "
            "the Tcl/Tk and IDLE feature.",
            file=sys.stderr,
        )
        return 1

    root = tk.Tk()
    SlideshowApp(root, tk, ttk, _image_dir(), _video_path(), _audio_paths())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
