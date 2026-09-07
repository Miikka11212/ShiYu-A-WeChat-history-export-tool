"""Explicitly labelled synthetic WeChat 4.x fixtures; never substitutes real data."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import zstandard
from .archive import build_archive

PEOPLE = [("wxid_demo_self", "我"), ("wxid_demo_lin", "林小满"), ("demo_team@chatroom", "新产品讨论组"),
          ("wxid_demo_zhou", "周予安"), ("demo_family@chatroom", "一家人"), ("filehelper", "文件传输助手"),
          ("wxid_demo_book", "读书搭子"), ("demo_weekend@chatroom", "周末出走计划")]


def make_source(root: Path):
    (root / "message").mkdir(parents=True)
    (root / "contact").mkdir()
    with closing(sqlite3.connect(root / "contact" / "contact.db")) as con:
        con.execute("CREATE TABLE contact(username TEXT, nick_name TEXT, remark TEXT, alias TEXT)")
        con.executemany("INSERT INTO contact VALUES(?,?,?,?)", [(u,n,"","") for u,n in PEOPLE])
        con.commit()
    with closing(sqlite3.connect(root / "message" / "message_0.db")) as con:
        con.execute("CREATE TABLE Name2Id(user_name TEXT)")
        con.executemany("INSERT INTO Name2Id VALUES(?)", [(u,) for u,n in PEOPLE])
        for i, (user, name) in enumerate(PEOPLE[1:]):
            table = "Msg_" + hashlib.md5(user.encode()).hexdigest()
            con.execute(f'CREATE TABLE "{table}" (local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content BLOB, packed_info_data BLOB)')
            start = datetime(2026, 9, 5, 9, 30) - timedelta(days=i)
            lines = ["早上好！今天的安排确认一下。", "好呀，资料我已经整理好了。", "这次的重点是把聊天记录好好保存下来，之后查找也会方便很多。", "收到，可以按时间和关键词筛选，也可以单独导出一个会话。", "那我们下午三点见，一起看看最终的效果。", "没问题，记得带上之前那份设计笔记。", "周末去山里走走吧，天气合适的话带上相机。", "可以！我把路线发到群里，我们慢慢商量。", "好的，这个计划就这样定了。", "下次再一起把那些有意思的小事记下来 🌿"]
            if i == 1:
                lines = ["这是合成数据的演示会话。", "Word 里会包含图片吗？", "已还原的图片会直接嵌入文档。", "那就用这组演示记录做验收。", "界面会清楚标注演示数据。"]
            for j, line in enumerate(lines):
                content = zstandard.ZstdCompressor().compress(line.encode()) if j % 3 == 0 else line
                con.execute(f'INSERT INTO "{table}" VALUES(?,?,?,?,?,?,?)', (j+1, 100000+i*100+j, 1, 1 if j%2 else i+2, int((start+timedelta(minutes=j*4)).timestamp()), content, None))
        con.commit()


def create_demo(output: Path, progress=lambda s: None, cancel=None):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".demo-", dir=output) as temp:
        root = Path(temp) / "wxid_demo_self_abcd" / "db_storage"
        make_source(root)
        path = build_archive(root, output, None, "wxid_demo_self", progress, cancel)
        with closing(sqlite3.connect(path)) as con:
            con.execute("UPDATE meta SET value='true' WHERE key='demo'")
            con.execute("UPDATE meta SET value=? WHERE key='source'", (json.dumps("合成演示数据", ensure_ascii=False),))
            con.commit()
        return path
