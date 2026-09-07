from pathlib import Path
from threading import Event
from types import SimpleNamespace
import sys
import pytest
from wxarchive import capture
from wxarchive.common import ArchiveError, Cancelled
from test_pipeline import make_encrypted_db


def fixture(monkeypatch, tmp_path, payloads, cancel=None):
    root = tmp_path / 'db_storage'
    (root / 'message').mkdir(parents=True)
    key, _ = make_encrypted_db(root / 'message' / 'message_0.db')
    process = SimpleNamespace(pid=123, info={'create_time': 1})
    state = {'attached': 0, 'detached': 0}

    class Script:
        def on(self, name, callback):
            self.callback = callback

        def load(self):
            for payload in payloads(key):
                self.callback({'type': 'send', 'payload': payload}, None)
            if cancel:
                cancel.set()

    class Session:
        def create_script(self, text):
            return Script()

        def detach(self):
            state['detached'] += 1

    def attach(pid):
        assert pid == 123
        state['attached'] += 1
        return Session()

    fake = SimpleNamespace(attach=attach, ProcessNotFoundError=type('Gone', (Exception,), {}),
                           InvalidOperationError=type('Invalid', (Exception,), {}),
                           PermissionDeniedError=type('Denied', (Exception,), {}))
    monkeypatch.setitem(sys.modules, 'frida', fake)
    monkeypatch.setattr(capture, 'own_wechat_processes', lambda: [process])
    return root, key, state


def test_accepts_only_database_authenticated_candidate_and_detaches(monkeypatch, tmp_path):
    root, key, state = fixture(monkeypatch, tmp_path, lambda k: [
        {'kind': 'ready'}, {'kind': 'candidate', 'value': '00' * 32},
        {'kind': 'candidate', 'value': k.value.hex()}])
    progress = []
    actual = capture.capture_key(root, progress.append, timeout=10)
    assert actual == key
    assert state == {'attached': 1, 'detached': 1}
    assert key.value.hex() not in '\n'.join(progress)


def test_cancel_detaches_process(monkeypatch, tmp_path):
    cancel = Event()
    root, _, state = fixture(monkeypatch, tmp_path, lambda _: [{'kind': 'ready'}], cancel)
    with pytest.raises(Cancelled):
        capture.capture_key(root, cancel=cancel, timeout=10)
    assert state['detached'] == 1


def test_timeout_without_process_does_not_attach(monkeypatch, tmp_path):
    root, _, state = fixture(monkeypatch, tmp_path, lambda _: [])
    monkeypatch.setattr(capture, 'own_wechat_processes', lambda: [])
    with pytest.raises(ArchiveError, match='未获取'):
        capture.capture_key(root, timeout=0.01)
    assert state == {'attached': 0, 'detached': 0}


def test_connected_without_key_explains_restart_instead_of_silent_wait(monkeypatch, tmp_path):
    from itertools import count
    root, _, state = fixture(monkeypatch, tmp_path, lambda _: [{'kind': 'ready'}])
    ticks = count(0, 5)
    monkeypatch.setattr(capture.time, 'monotonic', lambda: next(ticks))
    progress = []
    with pytest.raises(ArchiveError, match='未获取'):
        capture.capture_key(root, progress.append, timeout=80)
    assert any('秒' in note and '关闭窗口不等于退出' in note for note in progress)
    assert any('打开已有存档' in note for note in progress)
    assert state['detached'] == 1
