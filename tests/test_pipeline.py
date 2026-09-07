from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import Event
import hashlib
import hmac
import json
import os
import sqlite3
import struct
import zipfile

import pytest
from PIL import Image, ImageDraw
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import zstandard

from wxarchive.archive import Archive, build_archive
from wxarchive.common import ArchiveError, Cancelled
from wxarchive.crypto import Key, snapshot_db, verify_key
from wxarchive.docx_export import export_docx
from wxarchive.pictures import decode_dat, to_png

SELF, FRIEND, ROOM = 'wxid_test_self', 'wxid_test_friend', 'test_room@chatroom'
STAMP = int(datetime(2026, 9, 5, 10, 15).timestamp())
IMAGE_KEY = b'AbCdEfGh12345678'


def png_fixture():
    picture = Image.new('RGB', (840, 400), '#e5eee0')
    draw = ImageDraw.Draw(picture)
    draw.rectangle((35, 35, 805, 365), outline='#315c44', width=4)
    draw.text((70, 100), 'SYNTHETIC IMAGE', fill='#315c44', font_size=48)
    draw.text((70, 220), 'DOCX image embedding test 2026-09-05', fill='#315c44', font_size=28)
    output = BytesIO()
    picture.save(output, 'PNG')
    return output.getvalue()


def v2_fixture(plain, key=IMAGE_KEY, xor=0x45):
    prefix_size = 64
    prefix = plain[:prefix_size] + bytes([16]) * 16
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = enc.update(prefix) + enc.finalize()
    xor_size = 16
    return b'\x07\x08V2\x08\x07' + struct.pack('<II', prefix_size, xor_size) + b'\x00' + encrypted + plain[prefix_size:-xor_size] + bytes(v ^ xor for v in plain[-xor_size:])


def make_source(tmp_path):
    root = tmp_path / 'source' / (SELF + '_abcd') / 'db_storage'
    (root / 'message').mkdir(parents=True)
    (root / 'contact').mkdir()
    with closing(sqlite3.connect(root / 'contact' / 'contact.db')) as con:
        con.execute('CREATE TABLE contact(username, nick_name, remark, alias)')
        con.executemany('INSERT INTO contact VALUES(?,?,?,?)', [(SELF,'本人','',''),(FRIEND,'测试朋友','',''),(ROOM,'测试群聊','','')])
        con.commit()
    for shard, names in ((0,[SELF,FRIEND,ROOM]), (12,[ROOM,SELF,FRIEND])):
        with closing(sqlite3.connect(root / 'message' / f'message_{shard}.db')) as con:
            con.execute('CREATE TABLE Name2Id(user_name)')
            con.executemany('INSERT INTO Name2Id VALUES(?)',[(u,) for u in names])
            for user in (FRIEND,ROOM):
                table = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
                con.execute(f'CREATE TABLE "{table}" (local_id, server_id, local_type, real_sender_id, create_time, message_content, packed_info_data)')
                if shard==0:
                    con.execute(f'INSERT INTO "{table}" VALUES(1,100,1,?,?,?,NULL)',(names.index(FRIEND)+1,STAMP,zstandard.ZstdCompressor().compress('你好，这是完整的中文消息。'.encode())))
                    con.execute(f'INSERT INTO "{table}" VALUES(2,101,1,?,?,?,NULL)',(names.index(SELF)+1,STAMP+60,'收到，下面是图片。'))
                    if user == FRIEND:
                        con.execute(f'INSERT INTO "{table}" VALUES(3,102,3,?,?,?,NULL)',(names.index(FRIEND)+1,STAMP+120,'<msg><img/></msg>'))
                else:
                    con.execute(f'INSERT INTO "{table}" VALUES(1,200,1,?,?,?,NULL)',(names.index(SELF)+1,STAMP+180,'不同分库的发送者索引也需要正确。'))
            con.commit()
    digest = hashlib.md5(b'synthetic-photo').hexdigest()
    folder = root.parent / 'msg' / 'attach' / hashlib.md5(FRIEND.encode()).hexdigest() / '2026-09' / 'Img'
    folder.mkdir(parents=True)
    (folder / (digest+'.dat')).write_bytes(v2_fixture(png_fixture()))
    with closing(sqlite3.connect(root / 'message' / 'message_resource.db')) as con:
        con.execute('CREATE TABLE ChatName2Id(user_name)')
        con.execute('INSERT INTO ChatName2Id VALUES(?)',(FRIEND,))
        con.execute('CREATE TABLE MessageResourceInfo(chat_id,message_local_id,message_local_type,message_create_time,packed_info)')
        con.execute('INSERT INTO MessageResourceInfo VALUES(1,3,3,?,?)',(STAMP+120,b'\x12\x22\x0a\x20'+digest.encode()))
        con.execute('INSERT INTO MessageResourceInfo VALUES(1,3,3,?,?)',(STAMP+999,b'\x12\x22\x0a\x20'+b'0'*32))
        con.commit()
    return root


def archive_fixture(tmp_path):
    root = make_source(tmp_path)
    return Archive(build_archive(root, tmp_path/'output', None, SELF, image_keys=[IMAGE_KEY], image_xor=0x45))


def test_import_all_shards_sender_and_image_identity(tmp_path):
    root = make_source(tmp_path)
    originals={p:hashlib.sha256(p.read_bytes()).digest() for p in root.rglob('*.db')}
    archive=Archive(build_archive(root,tmp_path/'output',None,SELF,image_keys=[IMAGE_KEY],image_xor=0x45))
    assert archive.count()==7
    assert len(archive.conversations())==2
    rows=list(archive.messages(chats=[FRIEND]))
    assert [r['is_self'] for r in rows]==[0,1,0,1]
    assert rows[0]['text']=='你好，这是完整的中文消息。'
    data,status=archive.picture(rows[2]['id'])
    assert data.startswith(b'\x89PNG') and status=='原图'
    assert archive.meta['picture_count']==1 and archive.meta['missing_pictures']==0
    assert originals=={p:hashlib.sha256(p.read_bytes()).digest() for p in originals}


def test_docx_contains_images_time_and_participants(tmp_path):
    archive=archive_fixture(tmp_path)
    out=tmp_path/'result.docx'
    result=export_docx(archive,FRIEND,out)
    assert (result.messages,result.pictures,result.missing)==(4,1,0)
    with zipfile.ZipFile(out) as package:
        xml=package.read('word/document.xml').decode()
        assert '测试朋友' in xml and '10:15:00' in xml and 'wxid_test_friend' in xml
        assert '不同分库' in xml and 'wp:inline' in xml
        assert len([n for n in package.namelist() if n.startswith('word/media/')])==1
        rels=package.read('word/_rels/document.xml.rels').decode()
        assert 'TargetMode="External"' not in rels


def test_missing_image_is_explicit(tmp_path):
    root=make_source(tmp_path)
    archive=Archive(build_archive(root,tmp_path/'output',None,SELF))
    assert archive.meta['missing_pictures']==1
    result=export_docx(archive,FRIEND,tmp_path/'missing.docx')
    assert result.missing==1
    with zipfile.ZipFile(result.path) as z:
        assert '图片未导出' in z.read('word/document.xml').decode()


def test_cancel_does_not_publish_partial_output(tmp_path):
    archive=archive_fixture(tmp_path)
    event=Event()
    event.set()
    with pytest.raises(Cancelled):
        export_docx(archive,FRIEND,tmp_path/'cancel.docx',cancel=event)
    assert not (tmp_path/'cancel.docx').exists()
    assert not list(tmp_path.glob('.docx-*'))


def test_existing_output_is_not_overwritten(tmp_path):
    archive=archive_fixture(tmp_path)
    target=tmp_path/'existing.docx'
    target.write_bytes(b'keep')
    with pytest.raises(ArchiveError):
        export_docx(archive,FRIEND,target)
    assert target.read_bytes()==b'keep'


def test_v2_roundtrip_and_corruption():
    picture=png_fixture()
    assert decode_dat(v2_fixture(picture),[IMAGE_KEY])==picture
    with pytest.raises(ArchiveError):
        decode_dat(v2_fixture(picture),[b'WrongKey12345678'])
    broken=bytearray(v2_fixture(picture))
    broken[6:10]=b'\xff'*4
    with pytest.raises(ArchiveError):
        decode_dat(bytes(broken),[IMAGE_KEY])


def make_encrypted_db(path):
    # Start from a valid empty SQLite page, reserve 80 bytes, then let SQLite
    # allocate all records itself. The fixture encryptor is independent.
    plain=path.with_suffix('.plain')
    with closing(sqlite3.connect(plain)) as con:
        con.execute('PRAGMA page_size=4096')
        con.execute('VACUUM')
    page=bytearray(plain.read_bytes())
    page[20]=80
    page[105:107]=struct.pack('>H',4016)
    plain.write_bytes(page)
    with closing(sqlite3.connect(plain)) as con:
        con.execute('CREATE TABLE example(body TEXT)')
        con.executemany('INSERT INTO example VALUES(?)',[('测试数据'*100,) for _ in range(50)])
        con.commit()
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    master=b'k'*32
    salt=b's'*16
    enc=hashlib.pbkdf2_hmac('sha512',master,salt,256000,32)
    mac=hashlib.pbkdf2_hmac('sha512',enc,bytes(v^0x3a for v in salt),2,32)
    data=plain.read_bytes()
    encrypted=bytearray()
    for i in range(0,len(data),4096):
        number=i//4096+1
        iv=hashlib.md5(str(number).encode()).digest()
        encryptor=Cipher(algorithms.AES(enc),modes.CBC(iv)).encryptor()
        start=16 if number==1 else 0
        ct=encryptor.update(data[i+start:i+4016])+encryptor.finalize()
        signature=hmac.new(mac,ct+iv+struct.pack('<I',number),hashlib.sha512).digest()
        encrypted.extend((salt if number==1 else b'')+ct+iv+signature)
    path.write_bytes(encrypted)
    return Key(master), data


def test_decrypt_real_sqlite_and_authenticate_every_page(tmp_path):
    source=tmp_path/'encrypted.db'
    key,plain=make_encrypted_db(source)
    assert verify_key(source.read_bytes()[:4096],key)
    output=tmp_path/'decrypted.db'
    snapshot_db(source,output,key)
    with closing(sqlite3.connect(output)) as con:
        assert con.execute('SELECT count(*) FROM example').fetchone()[0]==50
    damaged=bytearray(source.read_bytes())
    damaged[4096+20]^=1
    source.write_bytes(damaged)
    with pytest.raises(ArchiveError,match='2'):
        snapshot_db(source,tmp_path/'bad.db',key)
    assert not (tmp_path/'bad.db').exists()
    assert not list(tmp_path.glob('.snapshot-*'))


def test_wrong_key_leaves_no_partial_file(tmp_path):
    source=tmp_path/'encrypted.db'
    make_encrypted_db(source)
    with pytest.raises(ArchiveError):
        snapshot_db(source,tmp_path/'bad.db',Key(b'z'*32))
    assert not (tmp_path/'bad.db').exists()


def test_wal_latest_committed_message_is_in_snapshot(tmp_path):
    source=tmp_path/'live.db'
    con=sqlite3.connect(source)
    try:
        con.execute('PRAGMA page_size=4096')
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_autocheckpoint=0')
        con.execute('CREATE TABLE entries(value)')
        con.execute('INSERT INTO entries VALUES(1)')
        con.commit()
        con.execute('INSERT INTO entries VALUES(2)')
        con.commit()
        wal=Path(str(source)+'-wal')
        before=wal.read_bytes()
        output=tmp_path/'snapshot.db'
        snapshot_db(source,output,None)
        assert wal.read_bytes()==before
        with closing(sqlite3.connect(output)) as check:
            assert check.execute('SELECT sum(value) FROM entries').fetchone()[0]==3
    finally:
        con.close()


def test_picture_path_cannot_escape_source(tmp_path):
    from wxarchive.pictures import PictureLoader
    root=tmp_path/'root'
    root.mkdir()
    outside=tmp_path/'secret.png'
    outside.write_bytes(png_fixture())
    loader=PictureLoader(root)
    data,status=loader.load({'conversation':FRIEND,'local_id':'1','time':STAMP},{'paths':['../secret.png',str(outside)],'hashes':[]})
    assert data is None


def test_import_cancel_rolls_back(tmp_path):
    root=make_source(tmp_path)
    event=Event()
    def cancel_after_start(text):
        event.set()
    with pytest.raises(Cancelled):
        build_archive(root,tmp_path/'output',None,SELF,cancel_after_start,event)
    assert not list((tmp_path/'output').iterdir())
