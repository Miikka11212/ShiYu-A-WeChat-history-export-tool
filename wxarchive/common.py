from pathlib import Path
from threading import Event
import re
import sqlite3


class ArchiveError(Exception):
    pass


class Cancelled(ArchiveError):
    pass


def checkpoint(cancel: Event | None):
    if cancel and cancel.is_set():
        raise Cancelled("操作已取消。")


def connect_ro(path: Path):
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    return con


def ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def safe_name(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:70] or "会话"
    if name.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name
