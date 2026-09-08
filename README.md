# 拾语 微信记录导出 Word

**简体中文** | [English](README.en.md)

Windows 桌面程序，界面只保留微信进程检测、聊天对象下拉框和 DOCX 导出。

## 下载 Windows 软件

**[下载 Windows x64 版（ZIP）](https://github.com/Miikka11212/ShiYu-A-WeChat-history-export-tool/releases/latest/download/Shiyu-Windows-x64.zip)** · [所有版本](https://github.com/Miikka11212/ShiYu-A-WeChat-history-export-tool/releases)

无需安装 Python 或 Git。下载后把整个 ZIP 解压到有写入权限的文件夹，双击 **Shiyu.exe**，保留旁边的 `_internal` 文件夹。不要在压缩包预览中直接运行。
首次使用选择自己的微信数据目录，再点击“开始检测微信”。界面目前为中文，发布包尚未数字签名。

Download the ZIP, extract it, and open **Shiyu.exe**. No Python or Git required.
Windows 10/11 x64; live import tested with WeChat 4.1.13.12.

GitHub 的 “Source code” ZIP 是源码，不含可运行程序；源码用户请先执行下面的安装步骤。

## 使用

1. 下载版双击解压后的 `Shiyu.exe`；源码版安装依赖后双击 `start.bat`。
2. 选择自己的微信数据目录并开始检测（配置过目录时自动检测）。完全退出微信，再从桌面重新启动并登录微信。
3. 等到软件显示“密钥已自动获取并校验”。在微信中打开一张聊天原图，然后点击“读取聊天与图片”。图片密钥尚未就绪时会继续等待，请保持图片窗口开启。
4. 在下拉框中选择联系人或群聊，点击“导出 Word (.docx)”。

默认数据位置保存在本机 `local.json` 中；此文件不提交到 Git。
首次使用可复制 `local.example.json` 为 `local.json` 并填写自己的微信数据位置，也可在界面中选择。
读取时暂停收发消息；如果微信文件正在变化，程序会停止本次导入并提示重试。
不会主动退出微信，不需要输入微信密码。

Word 按消息时间排序，包含完整日期、时间、发送者、聊天对象、文字及已还原图片。
私聊和群聊均可选择，图片实际嵌入 DOCX，不依赖旁边的图片文件夹。
缺失、未下载或不支持的图片会在原消息位置说明，不会把占位符计为成功。
使用缩略图时会明确标注。
引用消息按类型显示可读内容；引用图片会按同一会话内的原消息编号匹配并嵌入，无法匹配时明确说明。
已有存档重新导出也会使用最新的引用解析，无需重新读取微信。已经生成的旧 Word 不会自动改变。

成功读取后的 `.sqlite` 存档包含消息和已还原图片。下次可使用“打开已有存档”，无需重新登录。
聊天数据仅在本机处理；密钥不写入配置、不记录到日志。
存档和 Word 是可读的本地文件，请自行选择保存位置。

## 启动问题

- 请使用 `start.bat` 或 `dist\Shiyu\Shiyu.exe`。`build` 和 `artifacts` 内的 EXE 是构建中间文件，不是可运行的完整发布包。
- 提示缺少 `python312.dll` 时，检查 EXE 旁的 `_internal` 文件夹是否完整；不要只复制 EXE。
- “已连接微信”只代表已安装检测入口，不代表已获取密钥。若启动拾语时微信已登录，请保持拾语检测开启，从微信菜单完全退出微信，再启动并登录。只关闭微信窗口或打开聊天可能不会重新初始化数据库。
- 已有存档可点击“停止”，再点击“打开已有存档”，无需再次获取密钥。

## 当前验证范围

- 已通过合成数据测试：SQLCipher 4 逐页认证与解密、WAL 已提交事务合并、多分库和群聊发送者识别、V2 图片还原、图片嵌入 DOCX、取消和输出保护。
- 在此电脑安装的微信 `4.1.13.12` 中，已从二进制结构定位到与启动时获取方法相符的函数入口。
- 已在微信 `4.1.13.12` 完成真实登录后的获取、消息导入、原图与缩略图还原，以及引用图片与原消息匹配。其他 4.x 小版本尚未逐一验证。测试使用的私人记录不包含在仓库中。
- 图片支持已下载的 PNG/JPEG/GIF/WebP、旧 XOR、V1 和 V2 AES 格式；WXGF 尝试提取 HEVC 首帧。新版客户端的图片密钥布局或容器变化可能导致部分图片失败。
- 不做语音/视频转写、朋友圈、已删除消息恢复、云端下载、手机备份包解析。
- DOCX 的图片关系和内容已检查；当前运行环境没有 LibreOffice，未完成自动分页渲染验证。
- 打包时隔离外部工具的 DLL 搜索路径，避免同名 ICU DLL 导致 QtCore 启动错误；构建时保留发布目录中的用户存档和导出文件。

## 从源码运行

需要 Windows 10/11 x64、Python 3.12（已验证版本）：

```powershell
powershell -File setup.ps1
.\.venv\Scripts\python.exe main.py
```

程序启动参数：

```powershell
.\.venv\Scripts\python.exe main.py --source "D:\wx\xwechat_files" --data "D:\wechattest\data"
.\.venv\Scripts\python.exe main.py --archive "D:\path\archive.sqlite"
.\.venv\Scripts\python.exe main.py --demo
```

`--demo` 仅供开发测试，清楚标注合成数据，不会读取真实微信。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
powershell -File build.ps1
```

打包为 `dist\Shiyu\Shiyu.exe`，分发时需要保留整个 `Shiyu` 文件夹及其 `_internal` 目录。

## 实现

- `capture.py`：等待本用户微信进程及 Weixin.dll，临时观察 SHA-512 的 HMAC 输入，取得候选主密钥后用目标数据库的 HMAC 验证。取消、超时和完成都会解除连接。
- `crypto.py`：SQLCipher 4 参数与所有页面 HMAC 检查，验证 WAL 校验和并仅合并已提交帧；只操作输出副本。
- `archive.py`：联系人和多分库消息解析、独立离线存档。
- `pictures.py`：消息与资源表精确匹配，图片还原与本地归档。
- `docx_export.py`：按时间生成消息和内嵌图片。
- `simple_ui.py`：进程检测、单个对象选择和 Word 导出。

公开技术资料见 `docs/research.md`；许可证见 `THIRD_PARTY_NOTICES.md`。
英文逐文件说明见 [docs/architecture.md](docs/architecture.md)。

## 发布新版本

修改 `VERSION` 的版本号后推送到 `main`，GitHub Actions 会安装依赖、运行测试、构建程序，并测试解压后的 ZIP，再创建对应的 GitHub Release。也可在 Actions 中手动运行 Windows release；已有版本不会覆盖。

本地制作下载包：

```powershell
powershell -File build.ps1 -PackageOnly
.\.venv\Scripts\python.exe tools/package_release.py
```

下载包输出到 `artifacts/release/`，只从干净的构建目录收集程序及依赖，不打包日常使用的 `dist/Shiyu/data` 或 `local.json`。
