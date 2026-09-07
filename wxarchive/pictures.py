"""WeChat V1/V2 images -> standalone PNG bytes for embedded DOCX pictures."""
from collections import Counter
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
import hashlib
import re
import struct
import subprocess

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image, ImageOps, UnidentifiedImageError
from .common import ArchiveError, checkpoint, connect_ro
from .win_keys import image_signature

V1 = b'\x07\x08V1\x08\x07'
V2 = b'\x07\x08V2\x08\x07'
MAX_IMAGE = 80 * 1024 * 1024


def decode_dat(data: bytes, keys=(), xor_key=None) -> bytes:
    if image_signature(data):
        return data
    if data[:6] in (V1, V2):
        if len(data) < 31:
            raise ArchiveError('图片文件不完整')
        plain_size, xor_size = struct.unpack('<II', data[6:14])
        cipher_size = plain_size + 16 - plain_size % 16
        end = 15 + cipher_size
        raw_end = len(data) - xor_size
        if plain_size > MAX_IMAGE or end > raw_end or xor_size > len(data):
            raise ArchiveError('图片分段长度无效')
        candidates = [b'cfcd208495d565ef'] if data[:6] == V1 else list(keys)
        for key in candidates:
            decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            plain = decryptor.update(data[15:end]) + decryptor.finalize()
            pad = plain[-1]
            if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
                continue
            plain = plain[:-pad]
            if len(plain) != plain_size or not image_signature(plain):
                continue
            xor = xor_key
            if xor_size >= 2 and plain.startswith(b'\xff\xd8\xff'):
                a, b = data[-2] ^ 0xFF, data[-1] ^ 0xD9
                if a == b:
                    xor = a
            if xor_size >= 8 and plain.startswith(b'\x89PNG'):
                expected = b'IEND\xaeB`\x82'
                trial = data[-8] ^ expected[0]
                if all((v ^ trial) == expected[i] for i, v in enumerate(data[-8:])):
                    xor = trial
            if xor_size and xor is None:
                raise ArchiveError('尚未取得图片尾部解码参数')
            return plain + data[end:raw_end] + bytes(b ^ (xor or 0) for b in data[raw_end:])
        raise ArchiveError('图片密钥未找到或不匹配；请在微信中打开一张原图后重新读取')
    for signature in (b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF8', b'RIFF'):
        if len(data) < len(signature):
            continue
        xor = data[0] ^ signature[0]
        if all((data[i] ^ xor) == b for i, b in enumerate(signature)):
            return data.translate(bytes(b ^ xor for b in range(256)))
    raise ArchiveError('图片格式尚不支持')


def to_png(data: bytes) -> bytes:
    if data.startswith(b'wxgf'):
        # WXGF wraps a HEVC elementary stream. Locate its VPS NAL header.
        offsets = [m.start() for m in re.finditer(b'\x00\x00\x00\x01\x40', data)]
        if not offsets:
            offsets = [m.start() for m in re.finditer(b'\x00\x00\x01\x40', data)]
        if not offsets:
            raise ArchiveError('WXGF 图片没有可识别的 HEVC 图像流')
        import imageio_ffmpeg
        try:
            result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'hevc', '-i', 'pipe:0',
                                     '-frames:v', '1', '-vf', 'scale=2400:2400:force_original_aspect_ratio=decrease',
                                     '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'], input=data[offsets[0]:],
                                    capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0 or not result.stdout.startswith(b'\x89PNG'):
                raise ArchiveError('WXGF 图片解码失败')
            data = result.stdout
        except subprocess.TimeoutExpired:
            raise ArchiveError('WXGF 图片解码超时') from None
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 60_000_000:
                raise ArchiveError('图片像素数超过处理上限')
            image.load()
            converted = ImageOps.exif_transpose(image).convert('RGB')
            converted.thumbnail((2400, 2400))
            output = BytesIO()
            converted.save(output, 'PNG')
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ArchiveError('无法读取完整图片，文件可能尚未下载完成') from None


class PictureLoader:
    def __init__(self, root, resource_db=None, keys=(), xor=None, cancel=None):
        self.root = Path(root).resolve()
        self.keys, self.xor, self.cancel = keys, xor, cancel
        self.resource = connect_ro(resource_db) if resource_db and resource_db.exists() else None
        self.chat_ids = {}
        if self.resource:
            try:
                self.chat_ids = {r[1]: r[0] for r in self.resource.execute('SELECT rowid,user_name FROM ChatName2Id')}
            except Exception:
                self.resource.close()
                self.resource = None

    def close(self):
        if self.resource:
            self.resource.close()

    def load(self, msg, info):
        checkpoint(self.cancel)
        hashes = list(info.get('hashes', []))
        chat = msg['conversation']
        chat_id = self.chat_ids.get(chat)
        if self.resource and chat_id is not None:
            # Match the complete message identity; local_id alone can be reused.
            row = self.resource.execute('''SELECT packed_info FROM MessageResourceInfo
                WHERE chat_id=? AND message_local_id=? AND (message_local_type & 4294967295)=3
                AND message_create_time=? ORDER BY rowid DESC LIMIT 1''',
                (chat_id, int(msg['local_id']), msg['time'])).fetchone()
            if row and isinstance(row[0], bytes):
                marked = re.findall(rb'\x12\x22\x0a\x20([0-9a-fA-F]{32})', row[0])
                found = marked or re.findall(rb'(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])', row[0])
                hashes = [h.decode().lower() for h in found] + hashes
        candidates = []
        for raw in info.get('paths', []):
            if not isinstance(raw, str) or ':\\' in raw and not Path(raw).is_absolute() or '://' in raw:
                continue
            try:
                path = Path(raw.replace('\\', '/'))
                candidate = (path if path.is_absolute() else self.root / path).resolve()
                if candidate.is_relative_to(self.root) and candidate.is_file():
                    candidates.append(candidate)
            except (OSError, ValueError):
                continue
        folder = self.root / 'msg' / 'attach' / hashlib.md5(chat.encode()).hexdigest()
        if folder.is_dir():
            month = datetime.fromtimestamp(msg['time']).strftime('%Y-%m')
            month_dirs = [folder / month] + [d for d in folder.iterdir() if d.is_dir() and d.name != month]
            for digest in dict.fromkeys(hashes):
                if not re.fullmatch('[0-9a-fA-F]{32}', digest):
                    continue
                # Prefer full image over HD and thumbnail, across all month folders.
                for suffix in ('.dat', '_h.dat', '_t.dat', '.jpg', '.png'):
                    for directory in month_dirs:
                        candidate = directory / 'Img' / (digest + suffix)
                        if candidate.is_file() and candidate.resolve().is_relative_to(self.root):
                            candidates.append(candidate)
        if not candidates:
            return None, '图片未在本机找到，请在微信中下载原图后重新读取'
        errors = []
        for file in dict.fromkeys(candidates):
            checkpoint(self.cancel)
            try:
                if file.stat().st_size > MAX_IMAGE:
                    raise ArchiveError('图片文件超过 80 MB')
                png = to_png(decode_dat(file.read_bytes(), self.keys, self.xor))
                status = '缩略图' if file.stem.endswith('_t') else '高清预览图' if file.stem.endswith('_h') else '原图'
                return png, status
            except (ArchiveError, OSError) as exc:
                errors.append(str(exc))
        return None, errors[0] if errors else '图片无法解码'
