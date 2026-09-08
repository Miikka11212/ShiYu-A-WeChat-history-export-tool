# Shiyu — WeChat Chat History to Word

[简体中文](README.md) | **English**

A Windows desktop app that exports a selected local WeChat conversation to a Word document,
including message timestamps, sender names, text, and embedded images.

## Download the Windows app

**[Download for Windows x64 (ZIP)](https://github.com/Miikka11212/ShiYu-A-WeChat-history-export-tool/releases/latest/download/Shiyu-Windows-x64.zip)** · [All releases](https://github.com/Miikka11212/ShiYu-A-WeChat-history-export-tool/releases)

Extract the **entire ZIP** into a writable folder, then double-click **Shiyu.exe**.
Keep the `_internal` folder beside the executable. Do not launch it inside the ZIP preview.
You do not need Python, Git, or Microsoft Word to export.

Windows 10/11 x64. The app interface is currently in Chinese.
Live import has been tested with **WeChat 4.1.13.12**; other versions may need adaptation.
The portable build is not digitally signed.

GitHub's automatic **Source code** downloads do not include the compiled app.
If there is no release yet, follow the source setup instructions below.

## Using the app

1. Open the extracted `Shiyu.exe`. For a configured source installation, use `start.bat`.
2. Select your own WeChat account folder or `xwechat_files` directory using **浏览** (Browse).
   Click **开始检测微信** (Start detection). Detection starts automatically when a source is already configured.
3. Keep Shiyu open. **Fully exit WeChat from its menu**, start it again, and log in.
   Closing the WeChat window alone may leave the process running.
4. Wait for the message confirming the database key was captured and verified.
   Open an original chat image in WeChat, then click **读取聊天与图片** (Read chats and images).
   If the image key is not ready, keep the full-size image open while Shiyu waits.
5. Choose a contact or group from **聊天对象** (Conversation).
6. Click **导出 Word (.docx)** (Export Word) and choose the save location.

Pause sending and receiving messages while the data is being read.
If source files change during import, Shiyu stops and asks you to retry.
The app does not automatically close WeChat or ask for your WeChat password.

The data location can optionally be configured in a private `local.json` file.
Copy `local.example.json` to `local.json` in a source checkout and enter your own path,
or simply select the directory in the app. Private configuration is excluded from Git.

## What gets exported

- One selected direct conversation or group chat, in chronological order.
- Full dates and times, sender and conversation names, and message text.
- Locally restored images embedded directly in the DOCX; no companion image folder is required.
- Readable quoted messages. Quoted images are matched to the original message ID within the same conversation.
- Explicit explanations for missing or unsupported images. Thumbnail fallbacks are labelled.

Re-exporting an existing archive uses the current quote parser without importing WeChat again.
Previously generated Word documents do not update automatically.

## Archives and local data

Successful imports create a `.sqlite` archive containing messages and restored pictures.
Click **打开已有存档** (Open archive) to export it later without capturing keys again.
If detection is running, click **停止** (Stop) first.

Archives and exports default to the `data` folder beside the app.
These are readable local files. Keep your archives when upgrading, and do not share
the `data` folder or your private `local.json`.

Chat processing happens locally. Captured database and local image keys are not saved
as configuration or logged. Archived original message payloads may themselves contain
media metadata such as CDN parameters.

## Troubleshooting and limits

- **Missing Python DLL:** run the complete extracted app folder. Never copy just the EXE.
  `build` and `artifacts` contain development intermediates, not the everyday app.
- **Connected but waiting:** connection means the capture hook is ready, not that it has a key.
  Fully exit and restart WeChat while detection remains active, then log in.
  Opening another conversation may not reinitialize the database.
- **Image key not ready:** open a new original chat image and keep it visible, then retry reading.
- **Version compatibility:** real login, import, original/thumbnail image restoration, and quoted-image
  matching have been tested on WeChat 4.1.13.12. Other 4.x versions are not individually verified.
- Images: locally downloaded PNG/JPEG/GIF/WebP, legacy XOR, V1 and V2 AES formats are supported.
  WXGF attempts to decode the first HEVC frame. Changed formats or key layouts may fail.
- No voice/video transcription, Moments export, deleted-message recovery, cloud media downloads,
  or phone backup parsing.
- DOCX content and embedded image relationships have been checked. Automated page-by-page
  layout verification has not been completed because LibreOffice was unavailable in the development environment.

## Run from source

Use Windows x64 with **Python 3.12** (the tested Python version):

```powershell
powershell -File setup.ps1
.\.venv\Scripts\python.exe main.py
```

Optional commands:

```powershell
.\.venv\Scripts\python.exe main.py --source "D:\path\xwechat_files"
.\.venv\Scripts\python.exe main.py --archive "D:\path\archive.sqlite"
.\.venv\Scripts\python.exe main.py --data "D:\path\my-archives"
.\.venv\Scripts\python.exe main.py --demo
```

`--demo` generates clearly labelled synthetic records. It does not read WeChat.

## Test and build

```powershell
.\.venv\Scripts\python.exe -m pytest -q
powershell -File build.ps1
```

The runnable output is `dist\Shiyu\Shiyu.exe`.
The normal build preserves user data already in `dist\Shiyu` and isolates dependency
search paths to avoid unrelated ICU DLLs breaking Qt.

To create a clean downloadable ZIP:

```powershell
powershell -File build.ps1 -PackageOnly
.\.venv\Scripts\python.exe tools/package_release.py
```

The ZIP and SHA-256 checksum are written to `artifacts/release/`.
The packager uses clean build output, excludes personal data and configuration,
and runs the extracted app's offline self-test before publishing the local ZIP.

## Publish a release

Update the numeric version in `VERSION` and push to `main`.
The **Windows release** GitHub Actions workflow installs dependencies, runs tests,
builds the app, validates the extracted ZIP, and creates the corresponding GitHub Release.
It can also be started manually from Actions. Existing versions are never overwritten.

## How it works

- `capture.py`: detects the current user's WeChat process, temporarily observes supported SHA-512
  operations through Frida, and validates candidate keys against a real database page.
- `crypto.py`: authenticates and decrypts SQLCipher pages into copies, including committed WAL data.
- `archive.py`: resolves contacts and senders across database shards and creates an offline archive.
- `pictures.py`: matches local media to messages and restores supported image formats.
- `docx_export.py`: exports chronological messages and embedded images.
- `simple_ui.py`: provides the desktop detection, selection, and export interface.

See the [file-by-file walkthrough](docs/architecture.md),
[technical references](docs/research.md), and [third-party notices](THIRD_PARTY_NOTICES.md).
