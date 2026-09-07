# Third-party notices

The HMAC-ipad key-observation approach and WeChat database compatibility research
are informed by tzwkb/wechat-decrypt, https://github.com/tzwkb/wechat-decrypt.
The implementation here is reorganized for a GUI workflow, validates full-page
HMACs, resolves function boundaries through PE unwind metadata, and does not
kill WeChat or write raw keys to disk.

MIT License

Copyright (c) 2026 cocofeng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Image format and resource association research also consulted wx-cli,
https://github.com/emcd39/wechat-cli (Apache-2.0). This project implements those
formats in Python; it does not bundle the referenced Rust source. The full
reference license is retained in `docs/licenses/wx-cli-Apache-2.0.txt`.

Runtime dependencies: PySide6 / Qt (LGPL-3.0 or commercial), Frida (wxWindows
Library Licence 3.1), cryptography (Apache-2.0 / BSD), python-docx (MIT), Pillow
(MIT-CMU), zstandard (BSD), psutil (BSD), imageio-ffmpeg (BSD-2-Clause).
Dependency license metadata is included with the packaged application. Qt
libraries are dynamically distributed in `_internal` and remain replaceable.
FFmpeg is supplied by imageio-ffmpeg; see its bundled licensing information.
