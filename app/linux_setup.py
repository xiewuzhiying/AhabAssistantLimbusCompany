"""Native Linux setup, diagnostics and a preview of the owned automation session."""
import json
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


class ProfileCopy(QThread):
    result = Signal(str)

    def __init__(self, source, destination, parent):
        super().__init__(parent)
        self.source, self.destination = source, destination

    def run(self):
        try:
            from module.game_and_screen.automation_profile import clone_profile
            clone_profile(Path(self.source), Path(self.destination))
            self.result.emit('')
        except Exception as exc:
            self.result.emit(str(exc))


class FrameReader(QThread):
    frame = Signal(QImage)
    waiting = Signal()

    def run(self):
        from module.automation.screenshot import ScreenShot
        from module.game_and_screen.gamescope_launcher import GamescopeLauncher
        from module.game_and_screen.x11_handle import X11Handle
        handle = X11Handle()
        while not self.isInterruptionRequested():
            try:
                if not GamescopeLauncher().is_running():
                    self.waiting.emit()
                else:
                    handle.init_handle('LimbusCompany')
                    frame = ScreenShot.take_screenshot_x11(gray=False, handle=handle).convert('RGB')
                    image = QImage(frame.tobytes(), frame.width, frame.height, frame.width*3, QImage.Format.Format_RGB888).copy()
                    self.frame.emit(image)
            except Exception:
                self.waiting.emit()
            self.msleep(200)


class Preview(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('AALC 画面预览（只读）')
        self.resize(960, 560)
        self.picture = QLabel('等待当前 AALC 会话画面…', self)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        QVBoxLayout(self).addWidget(self.picture)
        self.reader = FrameReader(self)
        self.reader.frame.connect(self.show_frame)
        self.reader.waiting.connect(lambda: self.picture.setText('等待当前 AALC 会话画面…'))
        self.reader.finished.connect(self.close)
        self.reader.start()

    def show_frame(self, image):
        self.picture.setPixmap(QPixmap.fromImage(image).scaled(self.picture.size(), Qt.AspectRatioMode.KeepAspectRatio))

    def closeEvent(self, event):
        if self.reader.isRunning():
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
        self.copy_worker.start()

    def copied(self, error):
        self.details.setPlainText(error or '复制完成，请保存配置。')

    def save(self):
        try:
            apply_selection(self.profile.text(), self.api.currentText(), self)
            self.diagnose()
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, '配置未保存', str(exc))

    def closeEvent(self, event):
        if self.copy_worker and self.copy_worker.isRunning():
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
