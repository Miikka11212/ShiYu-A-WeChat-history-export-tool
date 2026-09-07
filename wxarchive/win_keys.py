"""Read-only, current-user Weixin memory scan; no injection, process control or dumps."""
from dataclasses import dataclass, field
from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
from collections import Counter
import re
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from .capture import own_wechat_processes
from .common import ArchiveError, checkpoint
from .crypto import Key, PAGE, SQLITE, verify_key
from .discovery import db_root, list_databases


@dataclass(repr=False)
class SessionKeys:
    databases: dict = field(default_factory=dict)
    images: list = field(default_factory=list)
    xor: int | None = None


def image_signature(data: bytes):
    return (data.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF8', b'wxgf'))
            or data.startswith(b'RIFF') and data[8:12] == b'WEBP')


def image_templates(root: Path):
    blocks, votes = [], Counter()
    folder = root.parent / 'msg' / 'attach'
    if not folder.is_dir():
        return [], None
    files = sorted(folder.rglob('*.dat'), key=lambda p: p.stat().st_mtime, reverse=True)
    for file in files[:100]:
        with file.open('rb') as f:
            head = f.read(31)
            if head[:6] != b'\x07\x08V2\x08\x07' or len(head) < 31:
                continue
            if len(blocks) < 3 and head[15:31] not in blocks:
                blocks.append(head[15:31])
            if int.from_bytes(head[10:14], 'little') >= 2:
                f.seek(-2, 2)
                end = f.read(2)
                if end[0] ^ 0xFF == end[1] ^ 0xD9:
                    votes[end[0] ^ 0xFF] += 1
    xor = votes.most_common(1)[0][0] if votes and votes.most_common(1)[0][1] >= 2 else None
    return blocks, xor


def scan_keys(source: Path, progress=lambda s: None, cancel=None, timeout=100, strict=True,
              images_only=False) -> SessionKeys:
    root = db_root(source)
    pages = {}
    for path in ([] if images_only else list_databases(root)):
        with path.open('rb') as f:
            page = f.read(PAGE)
        if page[:16] != SQLITE:
            pages[page[:16]] = page
    templates, xor = image_templates(root)
    result = SessionKeys(xor=xor)
    if not pages and not templates:
        return result
    processes = own_wechat_processes()
    if not processes:
        raise ArchiveError('请先启动并登录此账号的 Windows 微信，再点击「读取聊天与图片」。')
    processes.sort(key=lambda p: p.memory_info().rss, reverse=True)

    class MBI(C.Structure):
        _fields_ = [('BaseAddress', C.c_void_p), ('AllocationBase', C.c_void_p),
                    ('AllocationProtect', W.DWORD), ('PartitionId', W.WORD),
                    ('RegionSize', C.c_size_t), ('State', W.DWORD), ('Protect', W.DWORD), ('Type', W.DWORD)]
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
    kernel.VirtualQueryEx.restype = C.c_size_t
    kernel.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    kernel.ReadProcessMemory.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    db_pattern = re.compile(rb"[xX]'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")
    image_pattern = re.compile(rb'(?<![A-Za-z0-9])[A-Za-z0-9]{16}(?:[A-Za-z0-9]{16})?(?![A-Za-z0-9])')
    seen_images = set()
    checked_db = set()
    deadline = time.monotonic() + timeout
    total_bytes = 0
    opened = 0
    progress('正在查找本机微信图片密钥…' if images_only else '正在读取本机微信密钥与图片密钥…')
    for process in processes:
        checkpoint(cancel)
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, process.pid)
        if not handle:
            continue
        opened += 1
        try:
            address = 0
            while time.monotonic() < deadline:
                checkpoint(cancel)
                region = MBI()
                if not kernel.VirtualQueryEx(handle, C.c_void_p(address), C.byref(region), C.sizeof(region)):
                    break
                base, size = region.BaseAddress or 0, region.RegionSize
                if base + size <= address:
                    break
                address = base + size
                if region.State != 0x1000 or region.Protect & 0x100 or region.Protect & 0xFF not in (0x04, 0x08, 0x40, 0x80):
                    continue
                offset = 0
                while offset < size and time.monotonic() < deadline:
                    checkpoint(cancel)
                    amount = min(2 * 1024 * 1024, size - offset)
                    buf = C.create_string_buffer(amount)
                    read = C.c_size_t()
                    kernel.ReadProcessMemory(handle, C.c_void_p(base + offset), buf, amount, C.byref(read))
                    data = buf.raw[:read.value]
                    for match in db_pattern.finditer(data):
                        salt = bytes.fromhex(match[2].decode())
                        if salt not in pages or salt in result.databases:
                            continue
                        candidate = bytes.fromhex(match[1].decode())
                        if (salt, candidate) in checked_db:
                            continue
                        checked_db.add((salt, candidate))
                        key = Key(candidate, 'derived')
                        if verify_key(pages[salt], key):
                            result.databases[salt] = key
                    if templates and not result.images and size <= 128 * 1024 * 1024:
                        for match in image_pattern.finditer(data):
                            candidate = match[0][:16]
                            if candidate in seen_images:
                                continue
                            if len(seen_images) >= 800000:
                                break
                            seen_images.add(candidate)
                            dec = Cipher(algorithms.AES(candidate), modes.ECB()).decryptor()
                            plain = dec.update(b''.join(templates)) + dec.finalize()
                            if any(image_signature(plain[i:i+16]) for i in range(0, len(plain), 16)):
                                result.images.append(candidate)
                                break
                    total_bytes += read.value
                    if total_bytes // (32*1024*1024) != (total_bytes-read.value) // (32*1024*1024):
                        progress('图片密钥已找到。' if images_only and result.images else
                                 '正在查找图片密钥，请保持微信开启…' if images_only else
                                 f'已校验 {len(result.databases)}/{len(pages)} 个数据库密钥 · 图片密钥{"已找到" if result.images else "查找中"}')
                    if len(result.databases) == len(pages) and (not templates or result.images):
                        return result
                    offset += max(1, amount - 128) if amount > 128 else amount
        finally:
            kernel.CloseHandle(handle)
    if not opened:
        raise ArchiveError('Windows 拒绝读取微信进程。请以与微信相同的用户和权限运行拾语。')
    if strict and len(result.databases) < len(pages):
        raise ArchiveError(f'仅找到 {len(result.databases)}/{len(pages)} 个数据库密钥。请使用「重新检测」并重新启动微信获取。')
    return result


def wait_image_keys(source: Path, progress=lambda s: None, cancel=None, timeout=180):
    """Wait for viewing a photo to make the per-account image key available."""
    root = db_root(source)
    templates, xor = image_templates(root)
    if not templates:
        return SessionKeys(xor=xor)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint(cancel)
        progress('正在等待图片密钥。请在微信中打开一张尚未点开过的聊天图片，保持大图窗口开启。')
        result = scan_keys(root, lambda _: None, cancel,
                           timeout=min(30, max(1, deadline-time.monotonic())),
                           strict=False, images_only=True)
        if result.images:
            progress('图片密钥已校验，开始读取聊天和图片…')
            return result
        if cancel is not None:
            cancel.wait(1)
        else:
            time.sleep(1)
    raise ArchiveError('图片密钥尚未就绪。请在微信中打开一张新的聊天原图，再点击「读取聊天与图片」。')
