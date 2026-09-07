from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
import html
import json
import sqlite3
import sys

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QDate, QUrl
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut, QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QSplitter, QListWidget, QListWidgetItem, QTextBrowser, QFileDialog,
    QDialog, QFormLayout, QDialogButtonBox, QComboBox, QCheckBox, QDateEdit, QProgressBar,
    QPlainTextEdit, QMessageBox, QFrame, QStackedWidget, QAbstractItemView)

from .archive import Archive, build_archive
from .capture import capture_key
from .common import ArchiveError, Cancelled
from .crypto import Key
from .demo import create_demo
from .discovery import db_root, find_accounts, infer_self

STYLE = """
* { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #f6f7f3; color: #243c30; }
QWidget { color: #243c30; }
QLabel#brand { font-size: 24px; font-weight: 700; color: #315c44; }
QLabel#title { font-size: 23px; font-weight: 700; }
QLabel#hero { font-size: 34px; font-weight: 700; }
QLabel#muted { color: #7a867c; font-size: 12px; }
QLabel#badge { background: #e4eddf; color: #527246; border-radius: 10px; padding: 5px 11px; font-size: 11px; }
QLabel#demo { background: #fff0d0; color: #866027; border-radius: 7px; padding: 8px; }
QPushButton { background: #fff; color: #46604e; border: 1px solid #dce2d7; border-radius: 7px; padding: 9px 15px; }
QPushButton:hover { background: #ecf0e7; border-color: #b8c7ae; }
QPushButton:pressed { background: #dae5d3; }
QPushButton:disabled { color: #9ba499; background: #eef0ea; }
QPushButton#primary { background: #315c44; color: #fff; border: 1px solid #315c44; font-weight: 600; }
QPushButton#primary:hover { background: #244c35; }
QPushButton#primary:disabled { background: #9baa9c; border-color: #9baa9c; }
QPushButton#quiet { border: none; background: transparent; color: #688060; padding: 7px 10px; }
QLineEdit, QComboBox, QDateEdit, QPlainTextEdit { background: #fff; border: 1px solid #dce2d7; border-radius: 7px; padding: 9px; selection-background-color: #bdd5b0; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #8aa879; }
QFrame#sidebar { background: #eef1e9; border-right: 1px solid #dde3d7; }
QFrame#card { background: white; border: 1px solid #e0e5dc; border-radius: 12px; }
QListWidget { border: none; background: transparent; outline: none; }
QListWidget::item { padding: 14px 12px; margin: 3px 7px; border-radius: 8px; color: #52634f; }
QListWidget::item:selected { background: #dce7d3; color: #254831; }
QListWidget::item:hover { background: #e6ecde; }
QTextBrowser { border: none; background: #f6f7f3; padding: 12px; }
QProgressBar { background: #e4eadf; border: none; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: #76985f; }
QSplitter::handle { background: #e0e6da; width: 1px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #ccd5c5; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QCheckBox { spacing: 9px; padding: 5px; }
QStatusBar { background: #eef1e9; color: #728269; border-top: 1px solid #e0e5d9; }
"""


def label(text, name=None, wrap=False):
    obj = QLabel(text)
    if name:
        obj.setObjectName(name)
    obj.setWordWrap(wrap)
    obj.setTextFormat(Qt.TextFormat.PlainText)
    return obj


def button(text, callback=None, primary=False):
    obj = QPushButton(text)
    if primary:
        obj.setObjectName("primary")
    if callback:
        obj.clicked.connect(callback)
    return obj


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#315c44'))
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    painter.setPen(QColor('#eef5e4'))
    painter.setFont(QFont('Microsoft YaHei', 28, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, '语')
    painter.end()
    return QIcon(pixmap)


class Job(QThread):
    progress = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.cancel = Event()
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.operation(self.progress.emit, self.cancel)
        except Exception as exc:
            if self.cancel.is_set():
                self.error = "操作已取消。"
            elif isinstance(exc, (ArchiveError, OSError, sqlite3.DatabaseError)):
                self.error = str(exc)
            else:
                self.error = f"操作失败（{type(exc).__name__}）。请检查数据格式和文件权限。"


class ImportDialog(QDialog):
    def __init__(self, parent, default_source, data_dir):
        super().__init__(parent)
        self.setWindowTitle("导入 Windows 微信 4.x")
        self.resize(680, 440)
        self.data_dir = data_dir
        self.key_value = None
        self.worker = None
        self.want_close = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(label("把聊天记录留在本地", "title"))
        layout.addWidget(label("选择账号 → 获取密钥 → 在微信打开一张图片 → 导入存档", "muted"))
        form = QFormLayout()
        self.source = QLineEdit(str(default_source or ""))
        self.source.setPlaceholderText("账号目录或 db_storage 目录")
        row = QHBoxLayout()
        row.addWidget(self.source)
        self.browse = button("浏览", self.choose_source)
        row.addWidget(self.browse)
        self.detect = button("查找账号", self.discover)
        row.addWidget(self.detect)
        form.addRow("数据目录", row)
        self.self_id = QLineEdit()
        self.self_id.setPlaceholderText("本人的微信 ID，用于准确区分收发方向")
        form.addRow("本人微信 ID", self.self_id)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("自动获取，或在此粘贴 64 位十六进制密钥；明文库可留空")
        form.addRow("数据库密钥", self.key)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("账号主密钥（适用于全部分库）", "master")
        self.mode.addItem("单库派生密钥（仅适用于对应数据库）", "derived")
        row.addWidget(self.mode)
        self.capture = button("获取本机密钥", self.start_capture)
        row.addWidget(self.capture)
        form.addRow("密钥类型", row)
        layout.addLayout(form)
        self.info = label("自动获取会临时附加当前用户的微信进程；需要手动重启并登录微信。密钥仅保留在本次会话内。", "muted", True)
        layout.addWidget(self.info)
        layout.addWidget(label("源文件只读。读取时请暂停收发消息；如果文件发生变化，软件会停止并提示重试。", "muted", True))
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        layout.addWidget(self.bar)
        actions = QHBoxLayout()
        self.cancel_button = button("取消", self.cancel_or_close)
        self.submit = button("开始导入", self.validate, True)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit)
        layout.addLayout(actions)
        self.source.editingFinished.connect(self.update_self)
        self.update_self()

    def update_self(self):
        try:
            self.self_id.setText(infer_self(db_root(Path(self.source.text()))))
        except ArchiveError:
            pass

    def choose_source(self):
        folder = QFileDialog.getExistingDirectory(self, "选择账号或 db_storage 目录", self.source.text())
        if folder:
            self.source.setText(folder)
            self.update_self()

    def discover(self):
        accounts = find_accounts()
        # A user-supplied nonstandard location can also be searched one level deep.
        supplied = Path(self.source.text())
        if supplied.is_dir():
            accounts.extend(p for p in supplied.glob("*/db_storage") if (p / "message").is_dir())
        accounts = sorted(set(accounts), key=str)
        if not accounts:
            self.info.setText("默认位置没有找到账号。请使用「浏览」选择自定义微信目录。")
        elif len(accounts) == 1:
            self.source.setText(str(accounts[0]))
            self.update_self()
        else:
            from PySide6.QtWidgets import QInputDialog
            selected, ok = QInputDialog.getItem(self, "选择账号", "本机微信数据目录", list(map(str, accounts)), 0, False)
            if ok:
                self.source.setText(selected)
                self.update_self()

    def start_capture(self):
        try:
            source = db_root(Path(self.source.text()))
        except ArchiveError as exc:
            self.info.setText(str(exc))
            return
        self.worker = Job(lambda p, c: capture_key(source, p, c), self)
        self.worker.progress.connect(self.info.setText)
        self.worker.finished.connect(self.capture_finished)
        for widget in (self.source, self.browse, self.detect, self.capture, self.submit, self.key, self.mode):
            widget.setEnabled(False)
        self.bar.show()
        self.cancel_button.setText("停止获取")
        self.worker.start()

    def capture_finished(self):
        worker = self.worker
        self.worker = None
        self.bar.hide()
        for widget in (self.source, self.browse, self.detect, self.capture, self.submit, self.key, self.mode):
            widget.setEnabled(True)
        self.cancel_button.setText("取消")
        if worker.error:
            self.info.setText(worker.error)
        else:
            self.key_value = worker.result
            self.key.setText(worker.result.value.hex())
            self.mode.setCurrentIndex(0)
            self.info.setText("主密钥已校验。请保持微信登录并打开一张聊天图片，然后点击「开始导入」。")
        worker.result = None
        worker.deleteLater()
        if self.want_close:
            self.reject()

    def validate(self):
        try:
            self.selected_source = db_root(Path(self.source.text()))
            self.key_value = Key.parse(self.key.text(), self.mode.currentData()) if self.key.text().strip() else None
        except ArchiveError as exc:
            self.info.setText(str(exc))
            return
        self.accept()

    def cancel_or_close(self):
        if self.worker:
            self.worker.cancel.set()
            self.info.setText("正在停止获取并解除连接…")
        else:
            self.reject()

    def reject(self):
        if self.worker:
            self.want_close = True
            self.cancel_or_close()
            return
        self.key.clear()
        self.key_value = None
        super().reject()
