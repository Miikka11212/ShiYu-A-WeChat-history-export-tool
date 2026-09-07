from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Event
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import uuid

from .common import ArchiveError, checkpoint, connect_ro, ident
from .crypto import Key, fingerprint, snapshot_db
from .discovery import db_root, list_databases
from .messages import parse_message, protobuf_paths

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE conversations(id TEXT PRIMARY KEY, name TEXT NOT NULL, is_group INTEGER NOT NULL,
                           count INTEGER DEFAULT 0, last_time INTEGER DEFAULT 0);
CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation TEXT NOT NULL, sender TEXT NOT NULL,
 sender_id TEXT NOT NULL, is_self INTEGER NOT NULL, time INTEGER NOT NULL, kind TEXT NOT NULL,
 text TEXT NOT NULL, raw TEXT NOT NULL, server_id TEXT NOT NULL, source TEXT NOT NULL,
 local_id TEXT NOT NULL, media TEXT NOT NULL);
CREATE INDEX msg_chat_time ON messages(conversation,time,id);
CREATE INDEX msg_time ON messages(time,id);
CREATE INDEX msg_kind ON messages(kind);
CREATE TABLE pictures(message_id INTEGER PRIMARY KEY, data BLOB, status TEXT NOT NULL);
PRAGMA user_version=1;
"""


def tables(con):
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({ident(table)})")}


def build_archive(source: Path, output: Path, key: Key | None, self_id: str = "",
                  progress=lambda value: None, cancel: Event | None = None, image_keys=(), image_xor=None) -> Path:
    root = db_root(source)
    files = list_databases(root)
    output = output.resolve()
    if output == root or root in output.parents or output == root.parent or root.parent in output.parents:
        raise ArchiveError("存档目录必须位于微信账号目录之外。")
    output.mkdir(parents=True, exist_ok=True)
    final = output / (datetime.now().strftime("archive-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".sqlite")
    before = {p: fingerprint(p) for p in files}
    warnings = []
    with tempfile.TemporaryDirectory(prefix=".import-", dir=output) as temporary:
        work = Path(temporary)
        mirrored = []
        for i, path in enumerate(files):
            checkpoint(cancel)
            progress(f"校验与解密 {i + 1}/{len(files)} · {path.name}")
            target = work / "source" / path.relative_to(root)
            selected_key = key
            if isinstance(key, dict):
                with path.open('rb') as f:
                    selected_key = key.get(f.read(16))
            snapshot_db(path, target, selected_key, cancel)
            mirrored.append(target)
        names = {}
        contact = work / "source" / "contact" / "contact.db"
        if contact.exists():
            with closing(connect_ro(contact)) as con:
                if "contact" in tables(con) and "username" in columns(con, "contact"):
                    for row in con.execute('SELECT * FROM "contact"'):
                        d = dict(row)
                        username = str(d.get("username") or "")
                        if username:
                            names[username] = str(d.get("remark") or d.get("nick_name") or d.get("alias") or username)
                else:
                    warnings.append("contact.db 的联系人表不兼容，显示原始微信 ID。")
        else:
            warnings.append("没有 contact.db，显示原始微信 ID。")
        hash_to_name = {hashlib.md5(n.encode()).hexdigest(): n for n in names}
        name_maps = {}
        for path in mirrored:
            if not re.fullmatch(r"message_\d+\.db", path.name, re.I):
                continue
            with closing(connect_ro(path)) as con:
                mapping = {}
                if "Name2Id" in tables(con) and "user_name" in columns(con, "Name2Id"):
                    for row in con.execute('SELECT rowid, user_name FROM "Name2Id"'):
                        if row[1]:
                            mapping[int(row[0])] = str(row[1])
                            hash_to_name[hashlib.md5(str(row[1]).encode()).hexdigest()] = str(row[1])
                name_maps[path] = mapping
        archive_path = work / "archive.sqlite"
        count = 0
        table_count = 0
        parse_warnings = 0
        with closing(sqlite3.connect(archive_path)) as dest:
            dest.executescript(SCHEMA)
            for path, name_map in name_maps.items():
                with closing(connect_ro(path)) as con:
                    for table in tables(con):
                        if not re.fullmatch(r"Msg_[0-9a-fA-F]{32}", table):
                            continue
                        checkpoint(cancel)
                        cols = columns(con, table)
                        if not {"local_id", "create_time", "message_content", "local_type"}.issubset(cols):
                            raise ArchiveError(f"{path.name}/{table} 的消息结构不兼容，导入已停止。")
                        table_count += 1
                        chat_id = hash_to_name.get(table[4:].lower(), table)
                        chat_name = names.get(chat_id, chat_id if not chat_id.startswith("Msg_") else "未识别会话 · " + table[4:12])
                        dest.execute("INSERT OR IGNORE INTO conversations(id,name,is_group) VALUES(?,?,?)",
                                     (chat_id, chat_name, int(chat_id.endswith("@chatroom"))))
                        progress(f"解析会话 · {chat_name}（累计 {count:,} 条）")
                        for row in con.execute(f"SELECT * FROM {ident(table)} ORDER BY create_time, local_id"):
                            if count % 300 == 0:
                                checkpoint(cancel)
                            d = dict(row)
                            parsed = parse_message(d.get("message_content"), int(d.get("local_type") or 0), d.get("compress_content"))
                            parse_warnings += bool(parsed.warning)
                            sender_id = name_map.get(int(d.get("real_sender_id") or 0), "")
                            text = parsed.text
                            if chat_id.endswith("@chatroom") and ":\n" in text:
                                prefix, rest = text.split(":\n", 1)
                                if prefix in names or prefix in name_map.values() or prefix.startswith("wxid_"):
                                    sender_id = sender_id or prefix
                                    text = rest
                            is_self = bool(self_id and sender_id == self_id)
                            sender = "我" if is_self else names.get(sender_id, sender_id or "未知发送者")
                            stamp = int(d.get("create_time") or 0)
                            if stamp > 100_000_000_000:
                                stamp //= 1000
                            if stamp < 0 or stamp > 253402214400:
                                raise ArchiveError(f"{path.name} 存在不支持的消息时间戳。")
                            media = {"paths": parsed.paths + protobuf_paths(d.get("packed_info_data")), "hashes": parsed.hashes}
                            dest.execute("""INSERT INTO messages(conversation,sender,sender_id,is_self,time,kind,text,raw,
                                         server_id,source,local_id,media) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                         (chat_id, sender, sender_id, int(is_self), stamp, parsed.kind, text, parsed.raw,
                                          str(d.get("server_id") or ""), path.name + "/" + table,
                                          str(d["local_id"]), json.dumps(media, ensure_ascii=False)))
                            count += 1
            if not table_count:
                raise ArchiveError("未找到支持的 Msg_<hash> 消息表。没有生成空白存档。")
            from .pictures import PictureLoader
            loader = PictureLoader(root.parent, work / 'source' / 'message' / 'message_resource.db', image_keys, image_xor, cancel)
            picture_count = missing_pictures = 0
            try:
                dest.row_factory = sqlite3.Row
                images = dest.execute("SELECT id,conversation,local_id,time,media FROM messages WHERE kind='图片'")
                for row in images:
                    checkpoint(cancel)
                    progress(f'还原图片 · {picture_count + missing_pictures + 1}')
                    png, status = loader.load(dict(row), json.loads(row['media']))
                    dest.execute('INSERT INTO pictures VALUES(?,?,?)', (row['id'], png, status))
                    picture_count += png is not None
                    missing_pictures += png is None
            finally:
                loader.close()
            if missing_pictures:
                warnings.append(f'{missing_pictures} 条图片未能还原，导出文档会在对应位置说明原因。')
            if parse_warnings:
                warnings.append(f"{parse_warnings} 条内容未完全解析，原始内容保存在本地存档中。")
            if not self_id:
                warnings.append("未指定本人的微信 ID，未推断消息的收发方向。")
            dest.execute("""UPDATE conversations SET count=(SELECT count(*) FROM messages WHERE conversation=conversations.id),
                         last_time=coalesce((SELECT max(time) FROM messages WHERE conversation=conversations.id),0)""")
            meta = {"version": "1", "source": str(root), "media_root": str(root.parent),
                    "self_id": self_id, "created": datetime.now().isoformat(), "warnings": warnings,
                    "message_count": count, "database_count": len(files), "demo": False}
            meta['picture_count'] = picture_count
            meta['missing_pictures'] = missing_pictures
            dest.executemany("INSERT INTO meta VALUES(?,?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
            dest.commit()
            progress(f"建立索引 · {count:,} 条消息")
            dest.execute("PRAGMA optimize")
        for path in files:
            if fingerprint(path) != before[path]:
                raise ArchiveError("微信数据库在导入时发生变化，已取消本次存档。请完全退出微信后重试。")
        checkpoint(cancel)
        archive_path.rename(final)
    progress(f"导入完成 · {count:,} 条消息")
    return final


class Archive:
    def __init__(self, path: Path):
        self.path = path.resolve()
        if not self.path.is_file():
            raise ArchiveError("存档文件不存在。")
        try:
            with closing(connect_ro(self.path)) as con:
                if con.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ArchiveError("不支持的存档版本。请选择由拾语生成的 .sqlite 存档。")
                self.meta = {r[0]: json.loads(r[1]) for r in con.execute("SELECT key,value FROM meta")}
                con.execute("SELECT id,conversation,time,kind,text FROM messages LIMIT 1")
        except (sqlite3.DatabaseError, ValueError):
            raise ArchiveError("这不是有效的拾语存档。微信原始数据库请使用「导入微信」功能。") from None

    def conversations(self):
        with closing(connect_ro(self.path)) as con:
            return [dict(r) for r in con.execute("SELECT * FROM conversations ORDER BY last_time DESC,name")]

    def picture(self, message_id):
        with closing(connect_ro(self.path)) as con:
            if 'pictures' not in tables(con):
                return None, '此旧存档未包含图片，请重新读取微信'
            row = con.execute('SELECT data,status FROM pictures WHERE message_id=?', (message_id,)).fetchone()
            return (row[0], row[1]) if row else (None, '此消息没有已还原图片')

    def where(self, chats=None, query="", start=None, end=None, kind=""):
        conditions, args = [], []
        if chats is not None:
            if not chats:
                return " WHERE 0", []
            conditions.append("conversation IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(chats))
        if query:
            conditions.append("(instr(lower(text),lower(?))>0 OR instr(lower(sender),lower(?))>0)")
            args.extend((query, query))
        if start is not None:
            conditions.append("time>=?")
            args.append(start)
        if end is not None:
            conditions.append("time<?")
            args.append(end)
        if kind:
            conditions.append("kind=?")
            args.append(kind)
        return (" WHERE " + " AND ".join(conditions) if conditions else ""), args

    def messages(self, *, limit=None, offset=0, cancel=None, **filters):
        where, args = self.where(**filters)
        sql = "SELECT * FROM messages" + where + " ORDER BY time,id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        with closing(connect_ro(self.path)) as con:
            if cancel:
                con.set_progress_handler(lambda: int(cancel.is_set()), 5000)
            for row in con.execute(sql, args):
                checkpoint(cancel)
                yield dict(row)

    def count(self, **filters):
        where, args = self.where(**filters)
        with closing(connect_ro(self.path)) as con:
            return con.execute("SELECT count(*) FROM messages" + where, args).fetchone()[0]
