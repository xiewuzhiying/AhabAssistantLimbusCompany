"""Final session cleanup, owned by the main window until the thread finishes."""
from PySide6.QtCore import QThread


class ShutdownCleanup(QThread):
    def run(self):
        from module.game_and_screen import game_process
        from module.platform_compat import IS_LINUX
        from tasks.base.retry_monitor import retry_monitor

        retry_monitor.request_stop()
        try:
            if IS_LINUX:
                game_process.kill_game()
        finally:
            retry_monitor.stop(timeout=None)
