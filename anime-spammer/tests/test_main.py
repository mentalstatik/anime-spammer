import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import (
    ImageDisplayError,
    SlideshowController,
    AUDIO_INTERVAL,
    VIDEO_INTERVAL,
    STARTUP_WEBSITES,
    TIMEOUT_WEBSITE,
    WEBSITE_REDIRECT_DELAY,
    _audio_paths,
    _image_dir,
    _load_restart_state,
    _open_random_website,
    prepare_image,
    random_position,
)
import main


class FakeRoot:
    def __init__(self):
        self.callbacks = {}
        self.cancelled = []
        self.next_id = 0

    def after(self, milliseconds, callback):
        self.next_id += 1
        timer_id = f"timer-{self.next_id}"
        self.callbacks[timer_id] = (milliseconds, callback)
        return timer_id

    def after_cancel(self, timer_id):
        self.cancelled.append(timer_id)
        self.callbacks.pop(timer_id, None)

    def fire(self, timer_id):
        _, callback = self.callbacks.pop(timer_id)
        callback()


class NoShuffle:
    def shuffle(self, items):
        pass

    def randint(self, low, _high):
        return low


class HighestRandom:
    def __init__(self):
        self.bounds = []

    def shuffle(self, items):
        pass

    def randint(self, low, high):
        self.bounds.append((low, high))
        return high


class FakeProcess:
    def __init__(self, running=False):
        self.returncode = None if running else 0
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


class SlideshowControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_dir = Path(self.temp_dir.name)
        self.root = FakeRoot()
        self.statuses = []
        self.stats = []
        self.displayed = []
        self.closed_count = 0

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_controller(self, show_image=None):
        def default_show(path, on_close):
            self.displayed.append((path, on_close))

        def close_image():
            self.closed_count += 1

        return SlideshowController(
            self.root,
            self.image_dir,
            show_image or default_show,
            close_image,
            self.statuses.append,
            self.stats.append,
            rng=NoShuffle(),
        )

    def add_image_file(self, name="one.jpg"):
        path = self.image_dir / name
        path.write_bytes(b"test")
        return path

    def test_missing_folder_reports_helpful_error(self):
        controller = self.make_controller()
        controller.image_dir = self.image_dir / "missing"

        self.assertFalse(controller.start())
        self.assertIn("Image folder not found", self.statuses[-1])
        self.assertFalse(self.root.callbacks)

    def test_empty_folder_reports_helpful_error(self):
        controller = self.make_controller()

        self.assertFalse(controller.start())
        self.assertIn("No JPG, PNG, or WebP images found", self.statuses[-1])
        self.assertFalse(self.root.callbacks)

    def test_invalid_image_is_skipped_and_reported(self):
        self.add_image_file()

        def invalid_image(_path, _on_close):
            raise ValueError("not an image")

        controller = self.make_controller(invalid_image)
        controller.start()
        timer_id = controller.timer_id
        self.root.fire(timer_id)

        self.assertEqual(controller.open_images, 0)
        self.assertIsNone(controller.timer_id)
        self.assertTrue(any("Could not open one.jpg" in status for status in self.statuses))
        self.assertIn("No readable images found", self.statuses[-1])

    def test_display_error_is_reported_without_trying_every_image(self):
        self.add_image_file("one.jpg")
        self.add_image_file("two.jpg")

        def broken_display(_path, _on_close):
            raise ImageDisplayError("window manager error")

        controller = self.make_controller(broken_display)
        controller.start()
        self.root.fire(controller.timer_id)

        self.assertEqual(controller.open_images, 0)
        self.assertIsNotNone(controller.timer_id)
        self.assertIn("Could not display", self.statuses[-1])
        self.assertIn("window manager error", self.statuses[-1])
        self.assertEqual(len(controller._remaining_images), 1)

    def test_closing_window_updates_delay_and_schedules_next_image(self):
        self.add_image_file()
        controller = self.make_controller()
        controller.start()

        self.root.fire(controller.timer_id)
        self.assertEqual(controller.open_images, 1)
        self.assertEqual(len(self.displayed), 1)
        self.assertIsNotNone(controller.timer_id)
        self.assertEqual(self.root.callbacks[controller.timer_id][0], 10000)

        self.displayed[0][1]()

        self.assertEqual(controller.open_images, 0)
        self.assertEqual(controller.images_shown, 1)
        self.assertEqual(controller.delay, 9.91)
        self.assertEqual(
            self.stats[-1], "Delay: 9.91 seconds | Images shown: 1 | Open: 0"
        )
        self.assertEqual(len(self.root.callbacks), 1)
        self.assertEqual(next(iter(self.root.callbacks.values()))[0], 9910)

    def test_each_image_uses_current_cooldown_interval(self):
        self.add_image_file()
        controller = self.make_controller()
        controller.delay = 8.64
        controller.start()

        self.assertEqual(self.root.callbacks[controller.timer_id][0], 8640)
        self.root.fire(controller.timer_id)
        first_close = self.displayed[-1][1]
        self.assertEqual(self.root.callbacks[controller.timer_id][0], 8640)

        first_close()
        self.assertEqual(controller.delay, 8.55)
        self.assertEqual(self.root.callbacks[controller.timer_id][0], 8550)

        self.root.fire(controller.timer_id)
        self.assertEqual(len(self.displayed), 2)
        self.assertEqual(self.root.callbacks[controller.timer_id][0], 8550)

    def test_delay_stops_decreasing_at_half_a_second(self):
        self.add_image_file()
        controller = self.make_controller()
        controller.delay = 0.04
        controller.start()
        self.root.fire(controller.timer_id)

        self.displayed[-1][1]()

        self.assertEqual(controller.delay, main.MIN_DELAY)
        self.assertEqual(
            self.stats[-1], "Delay: 0.50 seconds | Images shown: 1 | Open: 0"
        )
        self.assertEqual(next(iter(self.root.callbacks.values()))[0], 500)

    def test_starts_next_cooldown_when_image_opens(self):
        self.add_image_file("a.jpg")
        self.add_image_file("b.jpg")
        controller = self.make_controller()
        controller.start()

        self.root.fire(controller.timer_id)
        self.assertEqual(controller.open_images, 1)
        self.assertIn("Open: 1", self.stats[-1])
        self.assertIsNotNone(controller.timer_id)
        self.assertEqual(self.root.callbacks[controller.timer_id][0], 10000)

        self.root.fire(controller.timer_id)

        self.assertEqual(controller.open_images, 2)
        self.assertIn("Open: 2", self.stats[-1])
        self.assertEqual(len(self.displayed), 2)
        self.assertIsNotNone(controller.timer_id)

    def test_closing_image_starts_cooldown_timer_from_close_time(self):
        self.add_image_file("a.jpg")
        self.add_image_file("b.jpg")
        controller = self.make_controller()
        controller.start()
        self.root.fire(controller.timer_id)
        pending = controller.timer_id

        self.displayed[0][1]()

        self.assertEqual(controller.delay, 9.91)
        self.assertIn(pending, self.root.cancelled)
        self.assertEqual(len(self.root.callbacks), 1)
        self.assertEqual(next(iter(self.root.callbacks.values()))[0], 9910)

    def test_repeats_are_avoided_until_each_file_has_been_tried(self):
        first = self.add_image_file("a.jpg")
        second = self.add_image_file("b.png")
        controller = self.make_controller()
        controller.start()

        self.root.fire(controller.timer_id)
        first_shown = self.displayed[-1][0]
        self.displayed[-1][1]()
        self.root.fire(controller.timer_id)
        second_shown = self.displayed[-1][0]

        self.assertNotEqual(first_shown, second_shown)
        self.assertEqual({first_shown, second_shown}, {first, second})

    def test_stop_cancels_pending_timer(self):
        self.add_image_file()
        controller = self.make_controller()
        controller.start()
        pending_timer = controller.timer_id

        controller.stop()

        self.assertEqual(self.root.cancelled, [pending_timer])
        self.assertFalse(self.root.callbacks)
        self.assertTrue(controller.stopped)

    def test_stop_closes_open_image_window(self):
        self.add_image_file()
        controller = self.make_controller()
        controller.start()
        self.root.fire(controller.timer_id)

        controller.stop()

        self.assertEqual(self.closed_count, 1)
        self.assertEqual(controller.open_images, 0)

    def test_video_schedules_at_random_few_minute_interval(self):
        self.add_image_file()
        video = self.image_dir / "rickroll.mp4"
        video.write_bytes(b"video")
        played = []
        controller = SlideshowController(
            self.root,
            self.image_dir,
            lambda _path, _on_close: None,
            lambda: None,
            self.statuses.append,
            self.stats.append,
            rng=NoShuffle(),
            video_path=video,
            play_video=lambda path: (played.append(path) or FakeProcess()),
        )
        controller.start()

        video_timer = controller.video_timer_id
        interval = self.root.callbacks[video_timer][0]
        self.assertEqual(interval, VIDEO_INTERVAL[0] * 1000)
        self.root.fire(video_timer)

        self.assertEqual(played, [video])
        self.assertIn("rickrolled", self.statuses)
        self.assertEqual(
            self.root.callbacks[controller.video_timer_id][0], VIDEO_INTERVAL[0] * 1000
        )

    def test_stop_cancels_video_schedule(self):
        self.add_image_file()
        video = self.image_dir / "rickroll.mp4"
        video.write_bytes(b"video")
        controller = SlideshowController(
            self.root,
            self.image_dir,
            lambda _path, _on_close: None,
            lambda: None,
            self.statuses.append,
            self.stats.append,
            rng=NoShuffle(),
            video_path=video,
            play_video=lambda _path: FakeProcess(),
        )
        controller.start()
        video_timer = controller.video_timer_id

        controller.stop()

        self.assertIn(video_timer, self.root.cancelled)
        self.assertFalse(self.root.callbacks)

    def test_stop_terminates_running_video_process(self):
        self.add_image_file()
        video = self.image_dir / "rickroll.mp4"
        video.write_bytes(b"video")
        process = FakeProcess(running=True)
        controller = SlideshowController(
            self.root,
            self.image_dir,
            lambda _path, _on_close: None,
            lambda: None,
            self.statuses.append,
            self.stats.append,
            rng=NoShuffle(),
            video_path=video,
            play_video=lambda _path: process,
        )
        controller.start()
        self.root.fire(controller.video_timer_id)
        self.assertIn(process, controller.video_processes)

        controller.stop()

        self.assertTrue(process.terminated)
        self.assertFalse(controller.video_processes)
        self.assertFalse(self.root.callbacks)

    def test_audio_schedules_random_pool_clips_every_10_to_30_seconds(self):
        self.add_image_file()
        first_audio = self.image_dir / "first.mp3"
        second_audio = self.image_dir / "second.mp3"
        first_audio.write_bytes(b"audio")
        second_audio.write_bytes(b"audio")
        played = []
        rng = HighestRandom()
        controller = SlideshowController(
            self.root,
            self.image_dir,
            lambda _path, _on_close: None,
            lambda: None,
            self.statuses.append,
            self.stats.append,
            rng=rng,
            audio_paths=[first_audio, second_audio],
            play_audio=lambda path: (played.append(path) or FakeProcess()),
        )
        controller.start()

        audio_timer = controller.audio_timer_id
        self.assertEqual(
            self.root.callbacks[audio_timer][0], AUDIO_INTERVAL[1] * 1000
        )
        self.root.fire(audio_timer)

        self.assertEqual(played, [second_audio])
        self.assertEqual(rng.bounds[:2], [(10, 30), (0, 1)])
        self.assertEqual(
            self.root.callbacks[controller.audio_timer_id][0],
            AUDIO_INTERVAL[1] * 1000,
        )

    def test_stop_cancels_audio_timer_and_terminates_audio_process(self):
        self.add_image_file()
        audio = self.image_dir / "sound.mp3"
        audio.write_bytes(b"audio")
        process = FakeProcess(running=True)
        controller = SlideshowController(
            self.root,
            self.image_dir,
            lambda _path, _on_close: None,
            lambda: None,
            self.statuses.append,
            self.stats.append,
            rng=NoShuffle(),
            audio_paths=[audio],
            play_audio=lambda _path: process,
        )
        controller.start()
        audio_timer = controller.audio_timer_id
        self.root.fire(audio_timer)
        pending_audio_timer = controller.audio_timer_id
        self.assertIn(process, controller.audio_processes)

        controller.stop()

        self.assertIn(pending_audio_timer, self.root.cancelled)
        self.assertTrue(process.terminated)
        self.assertFalse(controller.audio_processes)
        self.assertFalse(self.root.callbacks)


class ImageLoadingTests(unittest.TestCase):
    def test_controller_window_close_spawns_detached_frozen_executable(self):
        class Root:
            destroyed = False

            def destroy(self):
                self.destroyed = True

        class Controller:
            stopped = False
            stopped_by_app = False
            delay = 3.7
            images_shown = 11
            next_image_at = 150.0

            def stop(self):
                self.stopped_by_app = True

        app = main.SlideshowApp.__new__(main.SlideshowApp)
        app.root = Root()
        app.controller = Controller()
        app._s = type("Status", (), {"set": lambda _self, _value: None})()
        app.restart_state_path = Path("/tmp/aneemay-test-restart-state.json")
        app._website_redirect_at = 170.0
        app._website_opened = True
        app._timeout_website_opened = False
        app._website_redirect_timer_id = None
        app._code_entered = False
        args = []
        with (
            patch.object(main.sys, "frozen", True, create=True),
            patch.object(main.sys, "executable", "/opt/aneemay"),
            patch.object(main.sys, "argv", ["/opt/aneemay", "--example"]),
            patch.object(main.time, "time", return_value=120.0),
            patch.object(main, "_save_restart_state") as save_state,
            patch.object(main.os, "execv", side_effect=lambda *a: args.append(a)),
        ):
            app._on_close()

        self.assertEqual(args[0], ("/opt/aneemay", ["/opt/aneemay", "--example"]))
        self.assertEqual(
            save_state.call_args.args[1],
            {
                "delay": 3.7,
                "images_shown": 11,
                "next_image_at": 150.0,
                "website_opened": True,
                "timeout_website_opened": False,
                "code_entered": False,
                "website_redirect_at": 170.0,
            },
        )
        self.assertTrue(app.controller.stopped_by_app)
        self.assertTrue(app.root.destroyed)

    def test_controller_window_close_keeps_saved_state_for_relaunched_instance(self):
        class Root:
            def destroy(self):
                self.destroyed = True

        class Controller:
            stopped = False
            delay = 2.5
            images_shown = 7
            next_image_at = 140.0

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as directory:
            app = main.SlideshowApp.__new__(main.SlideshowApp)
            app.root = Root()
            app.root.destroyed = False
            app.controller = Controller()
            app.restart_state_path = Path(directory) / "restart-state.json"
            app._website_redirect_at = 195.0
            app._website_opened = True
            app._timeout_website_opened = False
            app._code_entered = False
            app._s = type("Status", (), {"set": lambda _self, _value: None})()
            with (
                patch.object(main.sys, "frozen", True, create=True),
                patch.object(main.sys, "executable", "/opt/aneemay"),
                patch.object(main.sys, "argv", ["/opt/aneemay"]),
                patch.object(main.os, "execv"),
            ):
                app._on_close()

            self.assertEqual(
                _load_restart_state(app.restart_state_path),
                {
                    "delay": 2.5,
                    "images_shown": 7,
                    "next_image_at": 140.0,
                    "website_opened": True,
                    "timeout_website_opened": False,
                    "code_entered": False,
                    "website_redirect_at": 195.0,
                },
            )
            self.assertTrue(app.root.destroyed)

    def test_controller_window_close_spawns_python_script(self):
        class Root:
            def destroy(self):
                pass

        class Controller:
            stopped = False
            delay = 10.0
            images_shown = 0
            next_image_at = None

            def stop(self):
                pass

        app = main.SlideshowApp.__new__(main.SlideshowApp)
        app.root = Root()
        app.controller = Controller()
        app._s = type("Status", (), {"set": lambda _self, _value: None})()
        app.restart_state_path = Path("/tmp/aneemay-test-restart-state.json")
        app._website_redirect_at = 100.0
        app._website_opened = True
        app._timeout_website_opened = False
        app._code_entered = False
        calls = []
        with (
            patch.object(main.sys, "frozen", False, create=True),
            patch.object(main.sys, "executable", "/usr/bin/python3"),
            patch.object(main.sys, "argv", ["./main.py"]),
            patch.object(main, "_save_restart_state"),
            patch.object(main.os, "execv", side_effect=lambda *a: calls.append(a)),
        ):
            app._on_close()

        self.assertEqual(
            calls[0],
            (
                str(Path("/usr/bin/python3").resolve()),
                [str(Path("/usr/bin/python3").resolve()), str(Path("./main.py").resolve())],
            ),
        )

    def test_controller_close_reports_spawn_failure_without_exiting(self):
        class Root:
            destroyed = False

            def destroy(self):
                self.destroyed = True

        class Controller:
            stopped = False
            delay = 10.0
            images_shown = 0
            next_image_at = None

            def stop(self):
                self.stopped = True

            def start(self):
                self.started_again = True

        class Status:
            value = ""

            def set(self, value):
                self.value = value

        app = main.SlideshowApp.__new__(main.SlideshowApp)
        app.root = Root()
        app.controller = Controller()
        app._s = Status()
        app.restart_state_path = Path("/tmp/aneemay-test-restart-state.json")
        app._website_redirect_at = 100.0
        app._website_opened = True
        app._timeout_website_opened = False
        app._code_entered = False
        app._website_redirect_timer_id = None
        app._schedule_timeout_website = lambda: None
        with (
            patch.object(main, "_save_restart_state"),
            patch.object(main.os, "execv", side_effect=OSError("exec failed")),
            patch.object(main.subprocess, "Popen", side_effect=OSError("launch failed")),
        ):
            app._on_close()

        self.assertIn("Could not restart aneemay", app._s.value)
        self.assertFalse(app.root.destroyed)
        self.assertTrue(app.controller.started_again)

    def test_restart_restores_delay_count_and_remaining_image_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            image_dir = Path(directory)
            (image_dir / "one.jpg").write_bytes(b"image")
            root = FakeRoot()
            controller = SlideshowController(
                root,
                image_dir,
                lambda _path, _on_close: None,
                lambda: None,
                lambda _status: None,
                lambda _stats: None,
                delay=4.25,
                images_shown=8,
                next_image_at=105.0,
            )
            with patch.object(main.time, "time", return_value=100.0):
                controller.start()

            self.assertEqual(controller.delay, 4.25)
            self.assertEqual(controller.images_shown, 8)
            self.assertEqual(root.callbacks[controller.timer_id][0], 5000)

    def test_saved_restart_state_can_be_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "restart-state.json"
            main._save_restart_state(
                state_path,
                {"delay": 2.75, "images_shown": 13, "next_image_at": 1234.5},
            )
            self.assertEqual(
                _load_restart_state(state_path),
                {"delay": 2.75, "images_shown": 13, "next_image_at": 1234.5},
            )

    def test_windows_restart_state_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(main.platform, "system", return_value="Windows"),
                patch.dict(main.os.environ, {"LOCALAPPDATA": directory}, clear=False),
            ):
                self.assertEqual(
                    main._restart_state_path(),
                    Path(directory) / "Aneemay" / "restart-state.json",
                )

    def test_saved_restart_state_can_record_startup_website_as_already_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "restart-state.json"
            main._save_restart_state(
                state_path,
                {
                    "delay": 2.75,
                    "images_shown": 13,
                    "next_image_at": 1234.5,
                    "website_opened": True,
                },
            )
            self.assertTrue(_load_restart_state(state_path)["website_opened"])

    def test_open_random_website_opens_exactly_one_listed_site(self):
        class ChooseLast:
            @staticmethod
            def choice(items):
                self.assertEqual(items, STARTUP_WEBSITES)
                return items[-1]

        with patch.object(main.webbrowser, "open", return_value=True) as open_browser:
            website = _open_random_website(ChooseLast())

        self.assertEqual(website, STARTUP_WEBSITES[-1])
        open_browser.assert_called_once_with(STARTUP_WEBSITES[-1], new=2)

    def test_open_random_website_reports_default_browser_failure(self):
        with patch.object(main.webbrowser, "open", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "default browser"):
                _open_random_website()

    def test_timeout_website_opens_after_three_minutes(self):
        class Root(FakeRoot):
            pass

        class Controller:
            stopped = False

        class Status:
            value = ""

            def set(self, value):
                self.value = value

        app = main.SlideshowApp.__new__(main.SlideshowApp)
        app.root = Root()
        app.controller = Controller()
        app._website_redirect_at = 1000 + WEBSITE_REDIRECT_DELAY
        app._website_redirect_timer_id = None
        app._code_entered = False
        app._timeout_website_opened = False
        app._s = Status()
        with patch.object(main.time, "time", return_value=1000):
            app._schedule_timeout_website()

        timer_id = app._website_redirect_timer_id
        self.assertEqual(app.root.callbacks[timer_id][0], 180000)
        with patch.object(main.webbrowser, "open", return_value=True) as open_browser:
            app.root.fire(timer_id)

        open_browser.assert_called_once_with(TIMEOUT_WEBSITE, new=2)
        self.assertTrue(app._timeout_website_opened)
        self.assertIn(TIMEOUT_WEBSITE, app._s.value)
        app._schedule_timeout_website()
        self.assertFalse(app.root.callbacks)

    def test_entering_code_cancels_timeout_website(self):
        class Controller:
            stopped = False

        app = main.SlideshowApp.__new__(main.SlideshowApp)
        app.root = FakeRoot()
        app.controller = Controller()
        app._website_redirect_at = 1200
        app._website_redirect_timer_id = None
        app._code_entered = False
        app._timeout_website_opened = False
        app._schedule_timeout_website()
        timer_id = app._website_redirect_timer_id

        app._mark_code_entered()

        self.assertTrue(app._code_entered)
        self.assertIn(timer_id, app.root.cancelled)
        self.assertIsNone(app._website_redirect_timer_id)

    def test_stop_clears_saved_state_for_fresh_next_launch(self):
        class Root:
            def destroy(self):
                pass

        class Controller:
            def stop(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "restart-state.json"
            state_path.write_text("{}")
            app = main.SlideshowApp.__new__(main.SlideshowApp)
            app.root = Root()
            app.controller = Controller()
            app.restart_state_path = state_path
            app._website_redirect_timer_id = None

            app.stop()

            self.assertFalse(state_path.exists())

    def test_audio_paths_discovers_all_mp3_files_in_frozen_bundle(self):
        import sys

        previous = getattr(sys, "_MEIPASS", None)
        with tempfile.TemporaryDirectory() as directory:
            sys._MEIPASS = directory
            media = Path(directory) / "media"
            media.mkdir()
            first = media / "first.mp3"
            second = media / "second.MP3"
            (media / "not-audio.txt").write_text("ignore")
            first.touch()
            second.touch()
            try:
                self.assertEqual(_audio_paths(), [first, second])
            finally:
                if previous is None:
                    del sys._MEIPASS
                else:
                    sys._MEIPASS = previous

    def test_random_position_stays_within_screen_bounds(self):
        class Midpoint:
            @staticmethod
            def randint(low, high):
                return (low + high) // 2

        x, y = random_position((1920, 1080), (640, 480), Midpoint())

        self.assertEqual((x, y), (640, 300))

    def test_video_path_uses_frozen_bundle_root(self):
        import sys
        from main import _video_path

        previous = getattr(sys, "_MEIPASS", None)
        sys._MEIPASS = "/tmp/aneemay-bundle"
        try:
            self.assertEqual(_video_path(), Path("/tmp/aneemay-bundle/media/rickroll.mp4"))
        finally:
            if previous is None:
                del sys._MEIPASS
            else:
                sys._MEIPASS = previous

    def test_video_launch_uses_requested_title_and_random_position(self):
        class Root:
            def winfo_screenwidth(self):
                return 1920

            def winfo_screenheight(self):
                return 1080

        class Midpoint:
            @staticmethod
            def randint(low, high):
                return (low + high) // 2

        app = main.SlideshowApp.__new__(main.SlideshowApp)
        app.root = Root()
        app.controller = type("Controller", (), {"rng": Midpoint()})()
        video = Path("/tmp/rickroll.mp4")
        with patch.object(main.shutil, "which", return_value="/usr/bin/ffplay"):
            with patch.object(main.subprocess, "Popen") as popen:
                app._play_video(video)

        args, kwargs = popen.call_args
        self.assertIn("rickrolled", args[0])
        self.assertEqual(kwargs["env"]["SDL_VIDEO_WINDOW_POS"], "640,300")
        self.assertEqual(args[0][-1], str(video))

    def test_audio_launch_uses_headless_ffplay(self):
        app = main.SlideshowApp.__new__(main.SlideshowApp)
        audio = Path("/tmp/sound.mp3")
        with patch.object(main.shutil, "which", return_value="/usr/bin/ffplay"):
            with patch.object(main.subprocess, "Popen") as popen:
                app._play_audio(audio)

        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            ["/usr/bin/ffplay", "-loglevel", "error", "-nodisp", "-autoexit", str(audio)],
        )
        self.assertIs(kwargs["stdout"], main.subprocess.DEVNULL)

    def test_windows_audio_reports_ffmpeg_install_instructions(self):
        app = main.SlideshowApp.__new__(main.SlideshowApp)
        with (
            patch.object(main.platform, "system", return_value="Windows"),
            patch.object(main.shutil, "which", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "ffplay.exe.*PATH"):
                app._play_audio(Path("sound.mp3"))

    def test_image_dir_uses_frozen_bundle_root(self):
        import sys

        previous = getattr(sys, "_MEIPASS", None)
        sys._MEIPASS = "/tmp/slideshow-bundle"
        try:
            self.assertEqual(_image_dir(), Path("/tmp/slideshow-bundle/images"))
        finally:
            if previous is None:
                del sys._MEIPASS
            else:
                sys._MEIPASS = previous

    def test_small_images_are_upscaled_without_changing_aspect_ratio(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.png"
            Image.new("RGB", (64, 32), color="red").save(path)

            self.assertEqual(prepare_image(path, (1000, 800)).size, (512, 256))

    def test_invalid_image_file_is_rejected(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")

        with tempfile.TemporaryDirectory() as directory:
            invalid_image = Path(directory) / "broken.png"
            invalid_image.write_bytes(b"not a valid image")

            from PIL import UnidentifiedImageError

            with self.assertRaises(UnidentifiedImageError):
                prepare_image(invalid_image, (200, 200))


if __name__ == "__main__":
    unittest.main()
