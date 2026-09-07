"""SQLCipher 4 / WCDB page decoder. All pages authenticate before publication.

4096-byte pages, AES-256-CBC, 80 reserve bytes, PBKDF2-SHA512.
This module never modifies input databases or silently ignores WAL files.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import Event
import hashlib
import hmac
import os
import shutil
import struct
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from .common import ArchiveError, checkpoint, connect_ro

PAGE = 4096
RESERVE = 80
SQLITE = b"SQLite format 3\x00"


@dataclass(repr=False)
class Key:
    value: bytes
    mode: str = "master"

    @classmethod
    def parse(cls, text: str, mode: str = "master"):
        text = text.strip()
        if text.lower().startswith("x'") and text.endswith("'"):
            text = text[2:-1]
        try:
            data = bytes.fromhex(text)
        except ValueError:
            raise ArchiveError("密钥必须为 64 个十六进制字符。") from None
        if len(data) != 32 or len(text) != 64 or mode not in ("master", "derived"):
            raise ArchiveError("密钥必须为 64 个十六进制字符；请选择主密钥或单库派生密钥。")
        return cls(data, mode)


def keys_for(key: Key, salt: bytes):
    enc = (hashlib.pbkdf2_hmac("sha512", key.value, salt, 256000, 32)
           if key.mode == "master" else key.value)
    mac = hashlib.pbkdf2_hmac("sha512", enc, bytes(x ^ 0x3A for x in salt), 2, 32)
    return enc, mac


def decode_page(page: bytes, number: int, enc: bytes, mac: bytes) -> bytes:
    if len(page) != PAGE:
        raise ArchiveError(f"数据库第 {number} 页不完整。")
    start = 16 if number == 1 else 0
    end = PAGE - RESERVE
    payload = page[start:end + 16] + struct.pack("<I", number)
    expected = hmac.new(mac, payload, hashlib.sha512).digest()
    if not hmac.compare_digest(expected, page[end + 16:]):
        raise ArchiveError(f"第 {number} 页校验失败：密钥不匹配、文件损坏或加密格式不兼容。")
    dec = Cipher(algorithms.AES(enc), modes.CBC(page[end:end + 16])).decryptor()
    plain = dec.update(page[start:end]) + dec.finalize()
    if number == 1:
        plain = SQLITE + plain
        if plain[16:18] != b"\x10\x00" or plain[20:24] != b"\x50\x40\x20\x20":
            raise ArchiveError("解密后的 SQLite 页头不兼容。")
    return plain + bytes(RESERVE)


def verify_key(page: bytes, key: Key) -> bool:
    try:
        decode_page(page, 1, *keys_for(key, page[:16]))
        return True
    except (ArchiveError, ValueError):
        return False


def fingerprint(path: Path):
    def stamp(p):
        try:
            s = p.stat()
            return s.st_size, s.st_mtime_ns
        except FileNotFoundError:
            return None
    return tuple(stamp(Path(str(path) + suffix)) for suffix in ("", "-wal", "-journal"))


def require_quiet(path: Path):
    for suffix, min_size in (("-wal", 32), ("-journal", 0)):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size > min_size:
            raise ArchiveError(f"{path.name} 有待处理的事务日志。请从托盘完全退出微信后重试；不要删除 WAL / journal 文件。")


def snapshot_db(src: Path, dst: Path, key: Key | None, cancel: Event | None = None):
    if src.resolve() == dst.resolve() or dst.exists():
        raise ArchiveError("输出文件已存在或与源文件相同。")
    journal = Path(str(src) + "-journal")
    if journal.exists() and journal.stat().st_size:
        raise ArchiveError("源文件有回滚日志，请完全退出微信后重试。")
    before = fingerprint(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=dst.parent)
    tmp = Path(temporary)
    try:
        encrypted = False
        enc = mac = None
        with os.fdopen(fd, "wb") as out, src.open("rb") as source:
            first = source.read(PAGE)
            if first[:16] == SQLITE:
                out.write(first)
                while chunk := source.read(1024 * 1024):
                    checkpoint(cancel)
                    out.write(chunk)
            else:
                encrypted = True
                if key is None:
                    raise ArchiveError(f"{src.name} 已加密。请先获取密钥或输入主密钥。")
                enc, mac = keys_for(key, first[:16])
                out.write(decode_page(first, 1, enc, mac))
                number = 2
                while page := source.read(PAGE):
                    checkpoint(cancel)
                    out.write(decode_page(page, number, enc, mac))
                    number += 1
        wal = Path(str(src) + "-wal")
        if wal.exists() and wal.stat().st_size > 32:
            apply_wal(wal, tmp, enc, mac, cancel)
        if fingerprint(src) != before:
            raise ArchiveError("源数据库在读取时发生变化，请完全退出微信后重试。")
        con = connect_ro(tmp)
        try:
            result = con.execute("PRAGMA quick_check").fetchall()
            if len(result) != 1 or result[0][0] != "ok":
                raise ArchiveError(f"{src.name} 的 SQLite 完整性检查失败。")
        finally:
            con.close()
        checkpoint(cancel)
        tmp.rename(dst)
    finally:
        tmp.unlink(missing_ok=True)


def wal_checksum(data: bytes, little: bool, seed=(0, 0)):
    if len(data) % 8:
        raise ArchiveError("WAL 校验区长度无效。")
    words = struct.unpack(("<" if little else ">") + "I" * (len(data) // 4), data)
    a, b = seed
    for i in range(0, len(words), 2):
        a = (a + words[i] + b) & 0xFFFFFFFF
        b = (b + words[i+1] + a) & 0xFFFFFFFF
    return a, b


def apply_wal(wal: Path, target: Path, enc=None, mac=None, cancel=None):
    """Replay checksum-verified committed frames into our private snapshot only."""
    frames = []
    last_commit, db_pages = 0, 0
    with wal.open("rb") as stream:
        header = stream.read(32)
        if len(header) < 32:
            raise ArchiveError("WAL 文件头不完整。")
        magic, version, page_size = struct.unpack(">III", header[:12])
        if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or page_size != PAGE:
            raise ArchiveError("WAL 格式不兼容。")
        little = magic == 0x377F0682
        checksum = wal_checksum(header[:24], little)
        if checksum != struct.unpack(">II", header[24:]):
            raise ArchiveError("WAL 文件头校验失败。")
        while True:
            checkpoint(cancel)
            frame_offset = stream.tell()
            frame = stream.read(24)
            if len(frame) < 24:
                break  # incomplete, uncommitted tail
            if frame[8:16] != header[16:24]:
                break  # previous WAL generation's recycled tail
            page = stream.read(PAGE)
            if len(page) != PAGE:
                break
            number, size = struct.unpack(">II", frame[:8])
            if not number:
                raise ArchiveError("WAL 页号无效。")
            checksum = wal_checksum(frame[:8] + page, little, checksum)
            if checksum != struct.unpack(">II", frame[16:24]):
                raise ArchiveError("WAL 消息日志校验失败，请停止微信写入后重新读取。")
            frames.append((number, frame_offset + 24))
            if size:
                last_commit, db_pages = len(frames), size
        if not last_commit:
            return
        if db_pages > max(target.stat().st_size // PAGE, max(n for n, _ in frames[:last_commit])):
            raise ArchiveError("WAL 提交的数据库大小无效。")
        with target.open("r+b") as out:
            for number, offset in frames[:last_commit]:
                checkpoint(cancel)
                stream.seek(offset)
                page = stream.read(PAGE)
                if enc is not None:
                    page = decode_page(page, number, enc, mac)
                if number <= db_pages:
                    out.seek((number - 1) * PAGE)
                    out.write(page)
            out.truncate(db_pages * PAGE)
