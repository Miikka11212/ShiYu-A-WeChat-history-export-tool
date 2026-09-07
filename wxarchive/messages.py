from dataclasses import dataclass, field
import base64
import html
import io
import re
import xml.etree.ElementTree as ET
import zstandard

TYPES = {1: "文字", 3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情",
         48: "位置", 49: "分享", 50: "通话", 10000: "系统", 10002: "撤回"}


def inner_content(node):
    if node is None:
        return ""
    return (node.text or "") + "".join(ET.tostring(child, encoding="unicode") for child in node)


def unwrap_nested_xml(value):
    value = value.strip()
    for _ in range(2):
        if value.startswith(('&lt;', '&amp;lt;')):
            value = html.unescape(value)
    return value


def nested_summary(content, message_type=0, depth=0):
    """Referenced content has its own type and XML/CDATA/escaped envelope."""
    if message_type == 1:
        return content or "[文字]"  # Preserve literal XML deliberately sent as text.
    if depth >= 5:
        return "[嵌套消息]"
    value = unwrap_nested_xml(content)
    if not message_type:
        for tag, kind in (("appmsg", 49), ("img", 3), ("videomsg", 43),
                          ("voicemsg", 34), ("emoji", 47), ("location", 48)):
            if re.search(r'<'+tag+r'(?:\s|>)', value):
                message_type = kind
                break
    if message_type in (3, 43, 47, 42, 50):
        return f"[{TYPES[message_type]}]"
    if '<' in value:
        result = parse_message(value, message_type or 49, _depth=depth + 1)
        return result.text
    if message_type and message_type != 1:
        return f"[{TYPES.get(message_type, '附件')}]"
    return content or "[消息内容不可用]"


@dataclass
class Parsed:
    text: str
    kind: str
    raw: str
    paths: list[str] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)
    warning: str = ""


def decode_text(value) -> tuple[str, str]:
    if value is None:
        return "", ""
    if isinstance(value, str):
        return value, ""
    data = bytes(value)
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as stream:
                data = stream.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("decompression limit")
        except (zstandard.ZstdError, ValueError):
            return "base64:" + base64.b64encode(bytes(value)).decode(), "压缩内容无法解码，已保留原始数据"
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError:
        return "base64:" + base64.b64encode(data).decode(), "非 UTF-8 内容，已保留原始数据"


def parse_message(value, local_type: int, compressed=None, *, _depth=0) -> Parsed:
    raw, warning = decode_text(value)
    if not raw and compressed:
        raw, warning = decode_text(compressed)
    base_type = local_type & 0xFFFFFFFF
    kind = TYPES.get(base_type, f"类型 {local_type}")
    parsed = Parsed(raw, kind, raw, warning=warning)
    if warning:
        parsed.text = f"[{kind} · {warning}]"
        return parsed
    # A group sender prefix may precede an XML document.
    xml_start = raw.find("<")
    if base_type == 1 or xml_start < 0:
        parsed.text = raw or f"[{kind}]"
        return parsed
    xml = raw[xml_start:]
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        parsed.warning = "包含不支持的 XML 声明，已保留原文"
        parsed.text = f"[{kind} · 内容暂未解析]"
        return parsed
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError:
        parsed.warning = "结构化消息不完整，原始内容保留在存档中"
        parsed.text = f"[{kind} · 内容暂未解析]"
        return parsed

    def text(tag):
        node = tree.find(".//" + tag)
        return (node.text or "").strip() if node is not None else ""

    for node in tree.iter():
        for name, value in list(node.attrib.items()) + [(node.tag, node.text or "")]:
            if name.lower() in {"filepath", "file_path", "path", "relativepath", "thumbfullpath", "fullpath"} and value:
                parsed.paths.append(value)
            if "md5" in name.lower() and re.fullmatch(r"[a-fA-F0-9]{32}", value):
                parsed.hashes.append(value.lower())
    if base_type == 49:
        app = tree if tree.tag == 'appmsg' else tree.find('.//appmsg')
        if app is None:
            parsed.text = '[分享 · 内容暂未解析]'
            return parsed
        # Direct children belong to this message; descendant searches can pick
        # a type or title from the quoted message instead.
        def app_text(tag):
            return (app.findtext(tag) or '').strip()

        app_type = app_text("type") or str(local_type >> 32)
        label = {"6": "文件", "5": "链接", "19": "合并转发", "57": "引用", "33": "小程序", "36": "小程序"}.get(app_type, "分享")
        parsed.kind = label
        lines = [f"[{label}] " + (app_text("title") or "未命名"), app_text("des"), app_text("url")]
        if app_type == "57":
            ref = app.find("refermsg")
            if ref is not None:
                ref_type = ref.findtext('type') or ''
                ref_type = int(ref_type) & 0xFFFFFFFF if ref_type.isdigit() else 0
                summary = nested_summary(inner_content(ref.find('content')), ref_type, _depth)
                lines.append("引用 " + (ref.findtext("displayname") or "未知发送者") + "：" + summary)
        if app_type == "19":
            record = inner_content(app.find("recorditem"))
            if record and not re.search(r'<!DOCTYPE|<!ENTITY', record, re.I):
                try:
                    forwarded = ET.fromstring(record)
                    for item in forwarded.iter("dataitem"):
                        value = item.findtext('datadesc') or item.findtext('datatitle') or ''
                        item_type = item.get('datatype', '')
                        if item_type == '1':
                            summary = value or '[文字]'
                        elif item_type in ('2', '4'):
                            summary = '[图片]' if item_type == '2' else '[视频]'
                        elif item_type == '8':
                            summary = '[文件] ' + (item.findtext('datatitle') or '未命名')
                        else:
                            summary = nested_summary(value, depth=_depth) or '[附件]'
                        lines.append((item.findtext("sourcename") or "未知发送者") + "：" + summary)
                except ET.ParseError:
                    parsed.warning = "合并转发的内部记录无法解析，已保留原文"
        parsed.text = "\n".join(line for line in lines if line)
    elif base_type == 48:
        loc = tree.find(".//location")
        parsed.text = "[位置] " + (loc.get("label", "") + " " + loc.get("poiname", "") if loc is not None else text("label"))
    elif base_type in (10000, 10002):
        parsed.text = text("replacemsg") or text("plain") or "".join(tree.itertext()).strip() or f"[{kind}]"
    elif base_type == 34:
        voice = tree.find(".//voicemsg")
        duration = voice.get("voicelength", "") if voice is not None else ""
        parsed.text = f"[语音 {int(duration) / 1000:g} 秒]" if duration.isdigit() else "[语音]"
    else:
        parsed.text = f"[{kind}] " + (text("title") or text("des"))
    return parsed


def display_text(message):
    """Reparse old archive records at export time without changing the archive."""
    kind = message.get('kind', '')
    raw = message.get('raw', '')
    if raw and kind in ('引用', '合并转发', '分享', '文件', '链接', '小程序'):
        return parse_message(raw, 49).text
    return message.get('text', '')


def quoted_images(raw, depth=0):
    """Return image references, never CDN URLs or inline encryption parameters."""
    if depth >= 5 or not isinstance(raw, str):
        return []
    value = unwrap_nested_xml(raw)
    start = value.find('<')
    if start < 0 or re.search(r'<!DOCTYPE|<!ENTITY', value, re.I):
        return []
    try:
        tree = ET.fromstring(value[start:])
    except ET.ParseError:
        return []
    app = tree if tree.tag == 'appmsg' else tree.find('.//appmsg')
    if app is None or app.findtext('type') != '57':
        return []
    ref = app.find('refermsg')
    if ref is None:
        return []
    ref_type = (ref.findtext('type') or '').strip()
    if ref_type == '3':
        return [{'server_id': (ref.findtext('svrid') or '').strip(),
                 'sender': ref.findtext('displayname') or '未知发送者'}]
    if ref_type == '49':
        return quoted_images(inner_content(ref.find('content')), depth + 1)
    return []


def protobuf_paths(blob, depth=0) -> list[str]:
    """Extract only path-looking UTF-8 values; unknown protobuf fields stay opaque."""
    if not isinstance(blob, bytes) or len(blob) > 1024 * 1024 or depth > 4:
        return []
    pos, result = 0, []

    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(blob):
                raise ValueError
            b = blob[pos]
            pos += 1
            value |= (b & 127) << shift
            if not b & 128:
                return value
        raise ValueError
    try:
        while pos < len(blob):
            tag = varint()
            wire = tag & 7
            if not tag:
                break
            if wire == 0:
                varint()
            elif wire in (1, 5):
                pos += 8 if wire == 1 else 4
            elif wire == 2:
                size = varint()
                if size > len(blob) - pos:
                    break
                value = blob[pos:pos + size]
                pos += size
                try:
                    s = value.decode("utf-8")
                    if ("/" in s or "\\" in s) and not any(ord(c) < 32 for c in s) and len(s) < 2048:
                        result.append(s)
                except UnicodeDecodeError:
                    pass
                result.extend(protobuf_paths(value, depth + 1))
            else:
                break
    except ValueError:
        pass
    return result
