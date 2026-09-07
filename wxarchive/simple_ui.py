"""Read WeChat, choose one conversation, export a Word document."""
from pathlib import Path
from datetime import datetime
from collections import Counter
import sys
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLineEdit, QComboBox, QFrame, QProgressBar, QFileDialog, QMessageBox, QCompleter)
from .archive import Archive, build_archive
from .common import ArchiveError, safe_name
from .discovery import db_root, infer_self
from .docx_export import export_docx
from .win_keys import wait_image_keys
from .capture import capture_key
from .widgets import Job, STYLE, app_icon, label, button


class Window(QMainWindow):
    def __init__(self, data_dir: Path, source=None):
        super().__init__()
        self.data_dir, self.archive, self.worker = data_dir, None, None
        self.pending_close = False
        self.master_key = None
        self.setWindowTitle('拾语 · 微信记录导出 Word')
        self.setWindowIcon(app_icon())
        self.resize(760, 555)
        self.setMinimumSize(680, 510)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 26, 32, 22)
        layout.setSpacing(18)
        header = QHBoxLayout()
        header.addWidget(label('拾语', 'brand'))
        header.addWidget(label('微信聊天记录 → Word', 'muted'))
        header.addStretch()
        header.addWidget(label('本地处理', 'badge'))
        layout.addLayout(header)
        self.card = QFrame()
        self.card.setObjectName('card')
        form_box = QVBoxLayout(self.card)
        form_box.setContentsMargins(24, 23, 24, 23)
        form_box.setSpacing(18)
        form = QFormLayout()
        form.setSpacing(14)
        self.source = QLineEdit(str(source or ''))
        self.source.textChanged.connect(self.source_changed)
        self.source.setPlaceholderText('微信账号目录或 xwechat_files 文件夹')
        row = QHBoxLayout()
        row.addWidget(self.source, 1)
        row.addWidget(button('浏览', self.choose_source))
        form.addRow('微信数据', row)
        row = QHBoxLayout()
        self.read_btn = button('开始检测微信', self.read_wechat)
        row.addWidget(self.read_btn)
        self.step_note = label('先开此软件，再重新启动并登录微信。', 'muted')
        row.addWidget(self.step_note)
        row.addStretch()
        form.addRow('', row)
        self.chats = QComboBox()
        self.chats.setEditable(True)
        self.chats.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.chats.setMinimumHeight(43)
        self.chats.lineEdit().setPlaceholderText('读取后选择联系人或群聊，可输入名字查找…')
        self.chats.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.chats.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.chats.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.chats.currentIndexChanged.connect(self.selection_changed)
        self.chats.setEnabled(False)
        form.addRow('聊天对象', self.chats)
        form_box.addLayout(form)
        self.detail = label('导出内容：每条消息的时间、发送者、聊天对象，以及嵌入的图片。', 'muted', True)
        form_box.addWidget(self.detail)
        self.export_btn = button('导出 Word (.docx)', self.export, True)
        self.export_btn.setMinimumHeight(46)
        self.export_btn.setEnabled(False)
        form_box.addWidget(self.export_btn)
        layout.addWidget(self.card)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.note = label('等待开始检测。获取成功后，无需手动填写密钥。', 'muted', True)
        layout.addWidget(self.note)
        layout.addStretch()
        footer = QHBoxLayout()
        self.offline_btn = button('打开已有存档', self.open_offline)
        self.advanced_btn = button('重新检测', self.restart_detection)
        footer.addWidget(self.offline_btn)
        footer.addWidget(self.advanced_btn)
        footer.addStretch()
        self.cancel_btn = button('停止', self.cancel)
        self.cancel_btn.hide()
        footer.addWidget(self.cancel_btn)
        layout.addLayout(footer)

    def choose_source(self):
        path = QFileDialog.getExistingDirectory(self, '选择微信数据目录', self.source.text())
        if path:
            self.source.setText(path)

    def source_changed(self):
        self.master_key = None
        self.read_btn.setText('开始检测微信')
        self.step_note.setText('先开此软件，再重新启动并登录微信。')

    def start_job(self, operation, success):
        if self.worker:
            return
        self.worker = Job(operation, self)
        self.worker.progress.connect(self.note.setText)
        self.worker.finished.connect(lambda: self.finished(success))
        self.card.setEnabled(False)
        self.offline_btn.setEnabled(False)
        self.advanced_btn.setEnabled(False)
        self.progress.show()
        self.cancel_btn.show()
        self.worker.start()

    def finished(self, success):
        job, self.worker = self.worker, None
        self.card.setEnabled(True)
        self.offline_btn.setEnabled(True)
        self.advanced_btn.setEnabled(True)
        self.progress.hide()
        self.cancel_btn.hide()
        if self.pending_close:
            job.deleteLater()
            self.close()
            return
        if job.error:
            self.note.setText(job.error)
        else:
            try:
                success(job.result)
            except (ArchiveError, OSError) as exc:
                self.note.setText(str(exc))
        job.result = job.operation = None
        job.deleteLater()

    def read_wechat(self):
        try:
            source = db_root(Path(self.source.text()))
        except ArchiveError as exc:
            self.note.setText(str(exc))
            return
        output = self.data_dir / 'archives'
        if self.master_key is None:
            self.note.setText('等待微信进程：请完全退出微信，再从桌面重新启动并登录。')
            self.start_job(lambda p,c: capture_key(source,p,c,timeout=600), self.key_ready)
            return
        master_key = self.master_key
        def operation(progress, cancel):
            keys = wait_image_keys(source, progress, cancel)
            return build_archive(source, output, master_key, infer_self(source), progress, cancel, keys.images, keys.xor)
        self.start_job(operation, self.load_archive)

    def key_ready(self, key):
        self.master_key = key
        self.read_btn.setText('读取聊天与图片')
        self.step_note.setText('获取成功。请在微信中打开一张图片。')
        self.note.setText('密钥已自动获取并校验。请在微信中打开一张聊天原图，然后点击「读取聊天与图片」。')

    def restart_detection(self):
        self.source_changed()
        self.read_wechat()

    def load_archive(self, path):
        selected = self.chats.currentData()
        selected_id = selected['id'] if selected else None
        self.archive = Archive(Path(path))
        self.chats.blockSignals(True)
        self.chats.clear()
        conversations = self.archive.conversations()
        name_counts = Counter(chat['name'] for chat in conversations)
        for chat in conversations:
            title = chat['name'] + ('  [群聊]' if chat['is_group'] else '')
            if name_counts[chat['name']] > 1:
                title += '  (' + chat['id'] + ')'
            self.chats.addItem(title, chat)
            if chat['id'] == selected_id:
                self.chats.setCurrentIndex(self.chats.count() - 1)
        self.chats.blockSignals(False)
        self.chats.setEnabled(self.chats.count() > 0)
        self.export_btn.setEnabled(self.chats.count() > 0)
        self.selection_changed()
        demo = '演示数据 · ' if self.archive.meta.get('demo') else ''
        missing = self.archive.meta.get('missing_pictures', 0)
        self.note.setText(f'{demo}已读取 {self.chats.count()} 个会话、{self.archive.meta["message_count"]:,} 条消息。'
                          + (f' {missing} 张图片未还原，导出时会说明原因。' if missing else ''))

    def selection_changed(self, *args):
        chat = self.chats.currentData()
        if chat:
            self.detail.setText(f'共 {chat["count"]:,} 条消息。Word 将按时间排列，包含发送者、聊天对象和已还原图片。')

    def open_offline(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开本地存档', str(self.data_dir / 'archives'), '拾语存档 (*.sqlite)')
        if path:
            try:
                self.load_archive(Path(path))
            except ArchiveError as exc:
                self.note.setText(str(exc))

    def export(self):
        chat = self.chats.currentData()
        if not self.archive or not chat:
            return
        if self.chats.currentText() != self.chats.itemText(self.chats.currentIndex()):
            self.note.setText('请从下拉列表选择一个确切的联系人或群聊。')
            return
        filename = safe_name(chat['name']) + datetime.now().strftime('-%Y%m%d-%H%M%S.docx')
        folder = self.data_dir / 'exports'
        folder.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, '保存聊天记录 Word 文档', str(folder / filename), 'Word 文档 (*.docx)')
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != '.docx':
            target = target.with_suffix('.docx')
            if target.exists():
                self.note.setText('补全扩展名后的文件已存在，请重新选择保存名称。')
                return
        overwrite = target.exists()
        archive = self.archive
        self.start_job(lambda p,c: export_docx(archive, chat['id'], target, p, c, overwrite), self.export_done)

    def export_done(self, result):
        self.note.setText(f'已导出 {result.messages:,} 条消息、{result.pictures} 张图片。' + (f' {result.missing} 张图片未导出，原因已标注。' if result.missing else ''))
        box = QMessageBox(self)
        box.setWindowTitle('Word 已保存')
        box.setText(str(result.path))
        box.setTextFormat(Qt.TextFormat.PlainText)
        open_doc = box.addButton('打开 Word', QMessageBox.ButtonRole.ActionRole)
        box.addButton('完成', QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() == open_doc:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.path)))

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.note.setText('正在停止并清理临时文件…')

    def closeEvent(self, event):
        if self.worker:
            self.pending_close = True
            self.cancel()
            event.ignore()
        else:
            event.accept()


def run(data_dir: Path, source=None, archive=None):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('拾语')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window(data_dir, source)
    if archive:
        window.load_archive(archive)
    window.show()
    if source and not archive:
        QTimer.singleShot(400, window.read_wechat)
    return app.exec()
