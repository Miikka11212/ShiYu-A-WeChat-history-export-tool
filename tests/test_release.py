import pytest
from tools.package_release import collect_runtime


def bundle(tmp_path):
    root = tmp_path / 'Shiyu'
    (root / '_internal').mkdir(parents=True)
    (root / 'Shiyu.exe').write_bytes(b'fake exe')
    (root / '_internal/python312.dll').write_bytes(b'fake dll')
    return root


def test_release_requires_complete_runtime(tmp_path):
    root = bundle(tmp_path)
    (root / '_internal/python312.dll').unlink()
    with pytest.raises(ValueError, match='Incomplete'):
        collect_runtime(root)


def test_release_rejects_working_app_with_private_data(tmp_path):
    root = bundle(tmp_path)
    (root / 'data').mkdir()
    (root / 'data/private.sqlite').write_bytes(b'private')
    with pytest.raises(ValueError, match='clean'):
        collect_runtime(root)


def test_release_rejects_private_file_inside_runtime(tmp_path):
    root = bundle(tmp_path)
    (root / '_internal/chat.docx').write_bytes(b'private')
    with pytest.raises(ValueError, match='personal-data'):
        collect_runtime(root)


def test_clean_runtime_has_portable_paths(tmp_path):
    files = collect_runtime(bundle(tmp_path))
    assert {dest.as_posix() for _, dest in files} == {
        'Shiyu/Shiyu.exe', 'Shiyu/_internal/python312.dll'}


def test_dependency_template_is_allowed_only_when_unmodified(tmp_path):
    import docx
    from pathlib import Path
    root = bundle(tmp_path)
    target = root / '_internal/docx/templates/default.docx'
    target.parent.mkdir(parents=True)
    target.write_bytes((Path(docx.__file__).parent / 'templates/default.docx').read_bytes())
    assert len(collect_runtime(root)) == 3
    target.write_bytes(b'private export')
    with pytest.raises(ValueError, match='template differs'):
        collect_runtime(root)
