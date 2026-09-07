"""Build a synthetic acceptance artifact and render our own UI offscreen."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import sys
import sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_pipeline import archive_fixture,FRIEND
from wxarchive.docx_export import export_docx
from wxarchive.simple_ui import Window
from wxarchive.widgets import STYLE
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

root=Path('artifacts/acceptance-v2').resolve()
root.mkdir(parents=True,exist_ok=True)
archive=archive_fixture(root)
with sqlite3.connect(archive.path) as con:
    con.execute("UPDATE meta SET value='true' WHERE key='demo'")
archive.meta['demo']=True
result=export_docx(archive,FRIEND,root/'sample.docx')
app=QApplication([])
QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')
QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\segoeui.ttf')
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
window=Window(root/'data',Path(r'D:\wx\xwechat_files'))
window.show()
app.processEvents()
window.grab().save(str(root/'ui-empty.png'))
window.load_archive(archive.path)
app.processEvents()
window.grab().save(str(root/'ui-ready.png'))
window.close()
print('Synthetic acceptance: messages',result.messages,'embedded images',result.pictures)
