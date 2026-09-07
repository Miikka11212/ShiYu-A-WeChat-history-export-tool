"""Offline packaged-runtime acceptance check. Uses only generated test records."""
from contextlib import closing
from io import BytesIO
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import tempfile
import traceback
import zipfile


def run(report: Path):
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        import frida
        import imageio_ffmpeg
        from PIL import Image
        from PySide6.QtWidgets import QApplication
        from .archive import Archive
        from .demo import create_demo
        from .docx_export import export_docx
        from .simple_ui import Window

        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix='package-test-', dir=report.parent) as directory:
            folder = Path(directory)
            archive = Archive(create_demo(folder / 'demo'))
            chat = archive.conversations()[0]
            msg = next(archive.messages(chats=[chat['id']]))
            quoted = list(archive.messages(chats=[chat['id']]))[1]
            raw_quote = ('<msg><appmsg><type>57</type><title>引用测试</title><refermsg><type>3</type>'
                         '<displayname>测试用户</displayname><svrid>' + msg['server_id'] + '</svrid>'
                         '<content>&lt;msg&gt;&lt;img aeskey="SYNTHETIC_INTERNAL_FIELD"/&gt;&lt;/msg&gt;</content>'
                         '</refermsg></appmsg></msg>')
            blob = BytesIO()
            Image.new('RGB', (640, 320), '#e5eee0').save(blob, 'PNG')
            with closing(sqlite3.connect(archive.path)) as con:
                con.execute("UPDATE messages SET kind='图片' WHERE id=?", (msg['id'],))
                con.execute('INSERT OR REPLACE INTO pictures VALUES(?,?,?)', (msg['id'], blob.getvalue(), '原图'))
                con.execute("UPDATE messages SET kind='引用',text='旧版内容',raw=? WHERE id=?", (raw_quote, quoted['id']))
                con.commit()
            result = export_docx(archive, chat['id'], folder / 'sample.docx')
            with zipfile.ZipFile(result.path) as doc:
                assert 'word/media/image1.png' in doc.namelist()
                assert 'TargetMode="External"' not in doc.read('word/_rels/document.xml.rels').decode()
                xml = doc.read('word/document.xml').decode()
                assert 'SYNTHETIC_INTERNAL_FIELD' not in xml
                assert '引用图片' in xml and xml.count('<wp:inline') == 2
            window = Window(folder / 'data')
            window.load_archive(archive.path)
            assert window.chats.count() == 7 and window.export_btn.isEnabled()
            window.close()
            app.processEvents()
            check = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'], capture_output=True,
                                   timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            assert check.returncode == 0
            payload = {'ok': True, 'frida': frida.__version__, 'conversations': 7,
                       'exported_messages': result.messages, 'embedded_pictures': result.pictures,
                       'scope': 'synthetic data only; no WeChat process accessed'}
        status = 0
    except Exception:
        payload = {'ok': False, 'error': traceback.format_exc()}
        status = 1
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return status
