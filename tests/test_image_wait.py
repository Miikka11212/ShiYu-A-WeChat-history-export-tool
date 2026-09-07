from threading import Event
import pytest
from wxarchive import win_keys
from wxarchive.common import Cancelled


def test_waits_until_photo_key_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(win_keys, 'db_root', lambda p: p)
    monkeypatch.setattr(win_keys, 'image_templates', lambda p: ([b'0'*16], 12))
    results = iter([win_keys.SessionKeys(), win_keys.SessionKeys(images=[b'k'*16], xor=12)])
    monkeypatch.setattr(win_keys, 'scan_keys', lambda *a, **kw: next(results))
    monkeypatch.setattr(win_keys.time, 'sleep', lambda _: None)
    result = win_keys.wait_image_keys(tmp_path, timeout=5)
    assert result.images == [b'k'*16]


def test_cancel_while_waiting_for_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(win_keys, 'db_root', lambda p: p)
    monkeypatch.setattr(win_keys, 'image_templates', lambda p: ([b'0'*16], None))
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        win_keys.wait_image_keys(tmp_path, cancel=cancel)
