"""Collect installed runtime copyright notices for the local Windows bundle."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import subprocess
import sys
import imageio_ffmpeg

base = Path(__file__).resolve().parents[1]
output = base / 'artifacts' / 'licenses'
output.mkdir(parents=True, exist_ok=True)
packages = ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6',
            'frida', 'cryptography', 'cffi', 'pycparser', 'python-docx', 'lxml',
            'typing_extensions', 'Pillow', 'zstandard', 'psutil', 'imageio-ffmpeg',
            'pyinstaller')
index = []
for package in packages:
    dist = distribution(package)
    folder = output / package
    folder.mkdir(exist_ok=True)
    (folder / 'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
    count = 0
    for file in dist.files or []:
        if not any('license' in part.lower() or part.lower().startswith(('copying', 'copyright', 'notice'))
                   for part in file.parts):
            continue
        source = Path(dist.locate_file(file))
        if not source.is_file():
            continue
        name = '__'.join(p for p in file.parts if p not in ('.', '..'))
        shutil.copyfile(source, folder / name)
        count += 1
    index.append(f'{dist.metadata["Name"]} {dist.version}: {count} notice files')
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    shutil.copyfile(python_license, output / 'Python-LICENSE.txt')
for source in (base / 'docs' / 'licenses').glob('*'):
    if source.is_file():
        shutil.copyfile(source, output / source.name)
ffmpeg = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-L'], capture_output=True,
                        text=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
(output / 'FFmpeg-version-and-license.txt').write_text(ffmpeg.stdout + ffmpeg.stderr, encoding='utf-8')
(output / 'INDEX.txt').write_text('\n'.join(index), encoding='utf-8')
print(f'Collected notices for {len(packages)} dependencies.')
