"""Native Linux setup, diagnostics and a preview of the owned automation session."""
import json
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from module.native_linux import check_profile, load_settings, report, save_settings
from module.preview_stream import PreviewStream


class ProfileCopy(QThread):
    result = Signal(str)

    def __init__(self, source, destination, parent):
        super().__init__(parent)
        self.source, self.destination = source, destination

    def run(self):
        try:
            from module.game_and_screen.automation_profile import clone_profile
            clone_profile(Path(self.source), Path(self.destination), self.isInterruptionRequested)
            self.result.emit('')
        except Exception as exc:
            self.result.emit(str(exc))


class FrameReader(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._latest = None
        self._pending = False

    def publish(self, image):
        with self._lock:
            self._latest = image
            self._pending = True

    def take_latest(self):
        with self._lock:
            pending, image = self._pending, self._latest
            self._pending = False
            self._latest = None
            return pending, image

    def run(self):
        from module.game_and_screen.gamescope_launcher import GamescopeLauncher
        launcher = GamescopeLauncher()
        stream = None
        owner = None
        try:
            while not self.isInterruptionRequested():
                started = time.monotonic()
                interval = .05
                try:
                    if stream and (launcher.process is not owner or not launcher.is_running()):
                        stream.close()
                        stream = None
                    if stream is None:
                        owner = launcher.process
                        owned_display = launcher.display_name() if launcher.is_running() else None
                        if owned_display and launcher.process is owner:
                            stream = PreviewStream(owned_display)
                    if stream is None:
                        self.publish(None)
                        interval = .2
                    else:
                        width, height, pixels = stream.read_frame(self.isInterruptionRequested)
                        if launcher.process is owner and launcher.is_running():
                            # QImage must own its buffer after the next capture starts.
                            self.publish(QImage(pixels, width, height, width * 3, QImage.Format.Format_RGB888).copy())
                        else:
                            self.publish(None)
                except (OSError, ValueError, EOFError, subprocess.SubprocessError):
                    if stream:
                        stream.close()
                        stream = None
                    self.publish(None)
                    interval = .2
                if not self.isInterruptionRequested():
                    self.msleep(max(0, int((interval - (time.monotonic() - started)) * 1000)))
        finally:
            if stream:
                stream.close()


class Preview(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('AALC 画面预览（只读）')
        self.resize(960, 560)
        self.picture = QLabel('等待当前 AALC 会话画面…', self)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        QVBoxLayout(self).addWidget(self.picture)
        self.reader = FrameReader(self)
        # Poll a single latest frame: queued per-frame signals accumulate latency
        # when the GUI thread is occupied by other work.
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(50)
        self.reader.finished.connect(self.close)
        self.reader.start()

    def refresh(self):
        pending, image = self.reader.take_latest()
        if pending:
            if image is None:
                self.picture.setText('等待当前 AALC 会话画面…')
            else:
                self.show_frame(image)

    def show_frame(self, image):
        self.picture.setPixmap(QPixmap.fromImage(image).scaled(self.picture.size(), Qt.AspectRatioMode.KeepAspectRatio))

    def closeEvent(self, event):
        if self.reader.isRunning():
            self.timer.stop()
            self.reader.requestInterruption()
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)


class LinuxSetup(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Linux 原生运行设置')
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('先在 Steam 成功启动游戏一次并退出。复制到独立目录后，AALC 与 Steam 使用各自设置。'))
        try:
            settings = load_settings()
        except (OSError, ValueError):
            from module.native_linux import default_profile
            settings = {'profile': str(default_profile()), 'graphics_api': 'd3d11'}
        self.profile = QLineEdit(settings['profile'])
        self.api = QComboBox()
        self.api.addItems(['auto', 'd3d11', 'd3d12'])
        self.api.setCurrentText(settings['graphics_api'])
        layout.addWidget(QLabel('独立 Proton compatdata 目录'))
        layout.addWidget(self.profile)
        layout.addWidget(QLabel('图形 API（D3D11 是兼容选项，不代表卡顿根因已修复）'))
        layout.addWidget(self.api)
        row = QHBoxLayout()
        for label, callback in [('从 Steam prefix 复制', self.copy_profile), ('选择现有独立 prefix', self.choose), ('保存', self.save)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        diagnostics = QPushButton('检查环境 / 刷新诊断')
        diagnostics.clicked.connect(self.diagnose)
        layout.addWidget(diagnostics)
        copy = QPushButton('复制诊断信息')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        layout.addWidget(copy)
        self.copy_worker = None
        self._closing = False
        self.diagnose()

    def diagnose(self):
        self.details.setPlainText(json.dumps(report(), ensure_ascii=False, indent=2))

    def choose(self):
        value = QFileDialog.getExistingDirectory(self, '选择已有独立 prefix')
        if value:
            self.profile.setText(value)

    def copy_profile(self):
        if self.copy_worker and self.copy_worker.isRunning():
            return
        source = QFileDialog.getExistingDirectory(self, '选择 Steam steamapps/compatdata/1973530')
        if not source:
            return
        self.details.setPlainText('正在复制，可能需要数分钟；失败后可直接重试。')
        self.copy_worker = ProfileCopy(source, self.profile.text(), self)
        self.copy_worker.result.connect(self.copied)
        self.copy_worker.finished.connect(self.finish_close)
        self.copy_worker.start()

    def copied(self, error):
        self.details.setPlainText(error or '复制完成，请保存配置。')

    def save(self):
        try:
            apply_selection(self.profile.text(), self.api.currentText(), self)
            self.diagnose()
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, '配置未保存', str(exc))

    def finish_close(self):
        if getattr(self, '_closing', False):
            self.close()

    def closeEvent(self, event):
        if self.copy_worker and self.copy_worker.isRunning():
            self._closing = True
            self.copy_worker.requestInterruption()
            self.details.setPlainText("正在取消复制并清理临时文件…")
            event.ignore()
            return
        super().closeEvent(event)


def apply_selection(profile, api, parent=None):
    from module.game_and_screen.automation_profile import require_stopped
    from module.game_and_screen.gamescope_launcher import GamescopeLauncher
    selection = {'profile': str(Path(profile).expanduser().resolve()), 'graphics_api': api}
    try:
        current = load_settings()
    except (OSError, ValueError):
        current = None
    if selection == current:
        return
    if GamescopeLauncher().is_running():
        raise RuntimeError('SESSION_BUSY: 请先停止当前任务，再切换配置')
    check_profile(Path(profile))
    require_stopped(Path(profile))
    if parent is not None and QMessageBox.question(parent, '切换运行配置', '保存这份独立配置，并用于之后的手动与定时任务？') != QMessageBox.StandardButton.Yes:
        return
    save_settings(profile, api)


class LinuxControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        setup = QPushButton('Linux 环境与独立配置')
        setup.clicked.connect(lambda: LinuxSetup(self).exec())
        self.preview = QPushButton('预览游戏画面')
        self.preview.clicked.connect(self.show_preview)
        row.addWidget(setup)
        row.addWidget(self.preview)
        self.viewer = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def refresh(self):
        from module.config import cfg
        from module.game_and_screen.gamescope_launcher import GamescopeLauncher
        self.setVisible(not cfg.simulator)
        self.preview.setEnabled(not cfg.simulator and GamescopeLauncher().is_running())

    def show_preview(self):
        if self.viewer is None or not self.viewer.isVisible():
            self.viewer = Preview(self)
        self.viewer.show()
        self.viewer.raise_()
