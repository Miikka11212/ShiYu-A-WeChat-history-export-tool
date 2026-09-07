import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from wxarchive.simple_ui import Window
from wxarchive.widgets import STYLE
from test_pipeline import archive_fixture


def test_dropdown_drives_selected_conversation(tmp_path):
    app=QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    window=Window(tmp_path/'data',Path(r'D:\wx\xwechat_files'))
    assert not window.export_btn.isEnabled()
    archive=archive_fixture(tmp_path)
    window.load_archive(archive.path)
    assert window.chats.count()==2
    assert window.export_btn.isEnabled()
    window.chats.setCurrentIndex(1)
    assert window.chats.currentData()['id'] in ('wxid_test_friend','test_room@chatroom')
    assert str(window.chats.currentData()['count']) in window.detail.text()
    window.close()


def test_changing_source_discards_previous_account_key(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window(tmp_path / 'data')
    window.key_ready(object())
    assert window.master_key is not None
    window.source.setText(str(tmp_path / 'another-account'))
    assert window.master_key is None
    assert window.read_btn.text() == '开始检测微信'
    window.close()
