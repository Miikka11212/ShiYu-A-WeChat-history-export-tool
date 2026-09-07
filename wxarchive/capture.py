"""User-initiated local key capture. Never starts/kills WeChat or saves keys.

HMAC-ipad observation approach informed by tzwkb/wechat-decrypt (MIT).
The hook additionally requires a PE x64 runtime-function boundary; no guessed
entry-point patching. Compatibility still requires a real client test.
"""
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Event
import os
import time
import psutil

from .common import ArchiveError, checkpoint
from .crypto import Key, PAGE, SQLITE, verify_key
from .discovery import db_root, list_databases

HOOK = r"""
'use strict';
if (Process.arch !== 'x64') throw new Error('UNSUPPORTED_ARCH');
let installed = false;
const observed = new Set();
const listeners = [];
function install(m) {
  if (installed || m.name.toLowerCase() !== 'weixin.dll') return;
  installed = true;
  try {
    const pe = m.base.add(m.base.add(0x3c).readU32());
    if (pe.readU32() !== 0x4550 || pe.add(24).readU16() !== 0x20b) throw new Error('PE_FORMAT');
    const sectionCount = pe.add(6).readU16();
    const sections = pe.add(24 + pe.add(20).readU16());
    const opt = pe.add(24);
    const exceptionRva = opt.add(112 + 3 * 8).readU32();
    const exceptionSize = opt.add(116 + 3 * 8).readU32();
    if (!exceptionRva || exceptionSize > m.size || exceptionRva + exceptionSize > m.size) throw new Error('NO_UNWIND');
    const entries = [];
    for (let i=0; i+12<=exceptionSize; i+=12) {
      const p=m.base.add(exceptionRva+i);
      entries.push([p.readU32(),p.add(4).readU32()]);
    }
    const constants = [];
    const executable = [];
    for (let i=0; i<sectionCount; i++) {
      const s=sections.add(i*40), size=s.add(8).readU32(), rva=s.add(12).readU32(), flags=s.add(36).readU32();
      if (!size || rva+size>m.size) continue;
      if (flags & 0x20000000) executable.push({base:m.base.add(rva),size:size});
      if (flags & 0x40000000) {
        for (const hit of Memory.scanSync(m.base.add(rva),size,'22 ae 28 d7 98 2f 8a 42')) constants.push(hit.address);
      }
    }
    const candidates=new Set();
    for (const region of executable) {
      for (const op of ['48 8d','4c 8d']) {
        for (const hit of Memory.scanSync(region.base,region.size,op)) {
          const p=hit.address;
          const mod=p.add(2).readU8();
          if ((mod & 0xc7)!==5) continue;
          const target=p.add(7).add(p.add(3).readS32());
          if (!constants.some(c=>c.equals(target))) continue;
          const rva=p.sub(m.base).toUInt32();
          const fn=entries.find(e=>e[0]<=rva && rva<e[1]);
          if (fn) candidates.add(fn[0]);
        }
      }
    }
    if (!candidates.size || candidates.size>8) throw new Error('SHA512_SIGNATURE');
    for (const rva of candidates) {
      listeners.push(Interceptor.attach(m.base.add(rva),{onEnter() {
        try {
          if (observed.size>=64) return;
          const b=new Uint8Array(this.context.rdx.readByteArray(128));
          for (let i=32;i<128;i++) if (b[i]!==0x36) return;
          const k=Array.from(b.slice(0,32),v=>(v^0x36).toString(16).padStart(2,'0')).join('');
          if (observed.has(k)) return;
          observed.add(k); send({kind:'candidate',value:k});
        } catch (_) {}
      }}));
    }
    send({kind:'ready'});
  } catch (_) { send({kind:'unsupported'}); }
}
const observer=Process.attachModuleObserver({onAdded(m) {
  // Module observers run before newly loaded module code: install immediately
  // so database initialization cannot race a deferred callback.
  if (m.name.toLowerCase()==='weixin.dll') install(m);
}});
"""


def own_wechat_processes():
    owner = psutil.Process().username()
    found = []
    for p in psutil.process_iter(["name", "username", "cmdline", "create_time"]):
        try:
            if p.info["name"].lower() != "weixin.exe" or p.info["username"] != owner:
                continue
            if any(s.startswith("--type=") for s in (p.info["cmdline"] or [])):
                continue
            found.append(p)
        except (psutil.Error, AttributeError):
            continue
    return found


def capture_key(source: Path, progress=lambda value: None, cancel: Event | None = None,
                timeout=150) -> Key:
    if os.name != "nt":
        raise ArchiveError("自动密钥获取仅支持 Windows x64。")
    import frida
    root = db_root(source)
    target = next(p for p in list_databases(root) if p.name.startswith("message_"))
    with target.open("rb") as f:
        page = f.read(PAGE)
    if page[:16] == SQLITE:
        raise ArchiveError("这个数据库已经是明文，直接导入即可。")
    queue = Queue(maxsize=128)
    seen, attached, sessions, scripts = set(), set(), [], []
    retry_after = {}
    had_process = False
    ready_at = None
    next_reminder = None
    deadline = time.monotonic() + timeout

    def on_message(message, data):
        if message.get("type") == "send" and isinstance(message.get("payload"), dict):
            payload = message["payload"]
            if payload.get("kind") in ("ready", "unsupported", "candidate"):
                try:
                    queue.put_nowait(payload)
                except Full:
                    pass
        elif message.get("type") == "error":
            try:
                queue.put_nowait({"kind": "unsupported"})
            except Full:
                pass
    progress("等待密钥：请手动退出并重新启动微信，然后登录。请保持此窗口开启。")
    try:
        while time.monotonic() < deadline:
            checkpoint(cancel)
            now = time.monotonic()
            if ready_at is not None and now >= next_reminder:
                elapsed = int(now - ready_at)
                progress(f'已连接微信，等待密钥 {elapsed} 秒。若微信已经登录，请从微信菜单完全退出（关闭窗口不等于退出），再重新启动并登录；保持此检测窗口开启。也可点击「停止」后打开已有存档。')
                next_reminder = now + 15
            processes = own_wechat_processes()
            if had_process and not processes:
                ready_at = next_reminder = None
                progress("已检测到微信退出。请重新启动微信并登录，软件会自动连接。")
            had_process = bool(processes)
            for process in processes:
                identity = (process.pid, process.info["create_time"])
                if identity in attached or time.monotonic() < retry_after.get(identity, 0):
                    continue
                try:
                    progress("检测到微信进程，正在准备获取。请保持软件开启并完成微信登录。")
                    session = frida.attach(process.pid)
                    sessions.append(session)
                    script = session.create_script(HOOK)
                    scripts.append(script)
                    script.on("message", on_message)
                    script.load()
                    attached.add(identity)
                except (frida.ProcessNotFoundError, frida.InvalidOperationError):
                    retry_after[identity] = time.monotonic() + 1
                    continue
                except frida.PermissionDeniedError:
                    raise ArchiveError("Windows 拒绝访问微信进程。请确认软件与微信在同一用户及权限级别运行。") from None
            try:
                msg = queue.get(timeout=0.12)
            except Empty:
                continue
            if msg["kind"] == "ready":
                ready_at = time.monotonic()
                next_reminder = ready_at + 15
                progress("已连接微信，等待登录时的数据库初始化。若微信已经登录，请完全退出并重新启动微信，再登录；仅打开会话可能无法获取密钥。")
            elif msg["kind"] == "unsupported":
                progress("当前微信版本未匹配到获取入口。可重新启动微信再试；若仍出现此提示，需要适配此版本。")
            elif msg["kind"] == "candidate":
                candidate = msg.get("value", "")
                if candidate in seen or len(seen) >= 128:
                    continue
                seen.add(candidate)
                try:
                    key = Key.parse(candidate)
                except ArchiveError:
                    continue
                if verify_key(page, key):
                    progress("主密钥已校验。请在微信打开一张聊天原图，再点击「读取聊天与图片」。")
                    return key
        raise ArchiveError("未获取到可用主密钥。请点击重新获取后手动重启并登录微信；此客户端版本也可能尚不兼容。")
    finally:
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass
        seen.clear()
        scripts.clear()
