"""Package only a clean PyInstaller staging directory; never package dist user data."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def collect_runtime(bundle: Path):
    bundle = bundle.resolve()
    required = [bundle / 'Shiyu.exe', bundle / '_internal' / 'python312.dll']
    if not all(p.is_file() for p in required):
        raise ValueError('Incomplete bundle: Shiyu.exe and _internal/python312.dll are required.')
    if {p.name for p in bundle.iterdir()} != {'Shiyu.exe', '_internal'}:
        raise ValueError('Use the clean artifacts/package-build/Shiyu folder, not a working app folder.')
    files = []
    for p in bundle.rglob('*'):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('Links are not allowed in a release bundle.')
        if not p.is_file():
            continue
        rel = p.relative_to(bundle)
        template = rel.as_posix() == '_internal/docx/templates/default.docx'
        if template:
            import docx
            installed = Path(docx.__file__).parent / 'templates/default.docx'
            if p.read_bytes() != installed.read_bytes():
                raise ValueError('Bundled Word template differs from the installed dependency.')
        if (p.name.lower() == 'local.json' or (p.suffix.lower() in {'.sqlite', '.db', '.docx', '.dat'} and not template)
                or any(part.lower() in {'data', 'exports', 'archives'} for part in rel.parts)):
            raise ValueError('Unexpected personal-data file in release bundle: ' + str(rel))
        files.append((p, Path('Shiyu') / rel))
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, default=ROOT / 'artifacts/package-build/Shiyu')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be a numeric major.minor.patch version.')
    files = collect_runtime(args.bundle)
    notices = ROOT / 'artifacts/licenses'
    if not (notices / 'INDEX.txt').is_file():
        raise ValueError('Dependency notices are missing; run build.ps1 first.')
    files += [(p, Path('Shiyu/licenses') / p.relative_to(notices))
              for p in notices.rglob('*') if p.is_file()]
    files += [(ROOT / name, Path('Shiyu') / name)
              for name in ('README.md', 'README.en.md', 'THIRD_PARTY_NOTICES.md', 'VERSION')]
    files.append((ROOT / 'docs/START-HERE.txt', Path('Shiyu/START-HERE.txt')))
    documentation = ['local.example.json', 'docs/architecture.md', 'docs/research.md',
                     'docs/licenses/GPL-3.0.txt', 'docs/licenses/LGPL-3.0.txt',
                     'docs/licenses/wx-cli-Apache-2.0.txt']
    files += [(ROOT / name, Path('Shiyu') / name) for name in documentation]
    output = ROOT / 'artifacts/release'
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'Shiyu-Windows-x64.zip'
    partial = output / 'Shiyu-Windows-x64.zip.tmp'
    try:
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for source, destination in sorted(files, key=lambda pair: str(pair[1])):
                archive.write(source, destination.as_posix())
        # Test the actual extracted download, not the developer's existing dist folder.
        with tempfile.TemporaryDirectory(prefix='release-check-', dir=ROOT / 'artifacts') as temp:
            folder = Path(temp)
            with zipfile.ZipFile(partial) as archive:
                bad = archive.testzip()
                if bad:
                    raise ValueError('Corrupt ZIP entry: ' + bad)
                archive.extractall(folder)
            report = folder / 'selftest.json'
            result = subprocess.run([str(folder / 'Shiyu/Shiyu.exe'), '--self-test', str(report)],
                                    timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
            payload = json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
            if result.returncode or payload.get('ok') is not True:
                raise RuntimeError('Extracted release self-test failed: ' + str(payload))
            (output / 'selftest.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (output / 'SHA256SUMS.txt').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'Verified v{version}: {target} ({target.stat().st_size:,} bytes)')
    print('SHA256: ' + digest)


if __name__ == '__main__':
    main()
