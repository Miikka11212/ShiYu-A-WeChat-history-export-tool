from pathlib import Path
import os
import re

from .common import ArchiveError


def find_accounts() -> list[Path]:
    """Only current-user well-known folders, no other profiles or drive sweep."""
    roots = {Path.home() / "Documents", Path.home()}
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as reg:
                roots.add(Path(os.path.expandvars(winreg.QueryValueEx(reg, "Personal")[0])))
        except OSError:
            pass
        for reg_path in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as reg:
                    location = winreg.QueryValueEx(reg, "FileSavePath")[0]
                    if location and location != "MyDocument:":
                        roots.add(Path(os.path.expandvars(location)))
            except OSError:
                pass
    accounts = set()
    for root in roots:
        for base in (root / "xwechat_files", root):
            if not base.is_dir():
                continue
            try:
                for item in base.glob("*/db_storage"):
                    if (item / "message").is_dir():
                        accounts.add(item.resolve())
            except OSError:
                continue
    return sorted(accounts, key=str)


def db_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    if (path / "db_storage").is_dir():
        path /= "db_storage"
    elif not (path / "message").is_dir() and path.is_dir():
        choices = [p for p in path.glob("*/db_storage") if (p / "message").is_dir()]
        if len(choices) == 1:
            path = choices[0]
        elif len(choices) > 1:
            raise ArchiveError("这个文件夹有多个微信账号，请选择其中一个账号目录。")
    if not path.is_dir() or not (path / "message").is_dir():
        raise ArchiveError("请选择一个微信 4.x 账号的目录或 db_storage 目录（内含 message 文件夹）。")
    return path


def infer_self(root: Path) -> str:
    name = root.parent.name
    return re.sub(r"_[0-9a-fA-F]{4,8}$", "", name) if name.startswith("wxid_") else ""


def list_databases(root: Path) -> list[Path]:
    messages = sorted(p for p in (root / "message").glob("message_*.db")
                      if re.fullmatch(r"message_\d+\.db", p.name, re.I))
    if not messages:
        raise ArchiveError("未找到 message_N.db；当前目录可能不是微信 4.x 消息库。")
    contact = root / "contact" / "contact.db"
    resource = root / "message" / "message_resource.db"
    return ([contact] if contact.exists() else []) + messages + ([resource] if resource.exists() else [])
