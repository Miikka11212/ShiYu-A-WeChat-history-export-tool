from contextlib import closing
from html import escape
import sqlite3
import zipfile
import pytest
from wxarchive.messages import parse_message, quoted_images
from wxarchive.docx_export import export_docx
from test_pipeline import archive_fixture, FRIEND


IMAGE_XML = '<?xml version="1.0"?><msg><img aeskey="SYNTHETIC_SECRET" cdnthumburl="INTERNAL_CDN" md5="' + 'a'*32 + '"/></msg>'


def quote(content, kind=3, encoding='escaped', server_id='102'):
    if encoding == 'escaped':
        content = escape(content)
    elif encoding == 'cdata':
        content = '<![CDATA[' + content + ']]>'
    return ('<msg><appmsg><title>这张照片很好看</title><type>57</type>'
            '<refermsg><type>' + str(kind) + '</type><svrid>' + server_id + '</svrid><displayname>测试用户</displayname>'
            '<content>' + content + '</content></refermsg></appmsg></msg>')


@pytest.mark.parametrize('encoding', ['escaped', 'cdata'])
def test_quoted_photo_xml_becomes_readable_label(encoding):
    raw = quote(IMAGE_XML, encoding=encoding)
    parsed = parse_message(raw, (57 << 32) | 49)
    assert parsed.text == '[引用] 这张照片很好看\n引用 测试用户：[图片]'
    assert parsed.raw == raw


@pytest.mark.parametrize('kind,label', [(3,'图片'),(43,'视频'),(47,'表情'),(34,'语音')])
def test_quote_different_attachment_types(kind, label):
    value = parse_message(quote('<msg><metadata aeskey="SECRET"/></msg>', kind), 49)
    assert f'引用 测试用户：[{label}]' in value.text
    assert 'SECRET' not in value.text


def test_nested_file_and_nested_quote():
    file_xml = '<appmsg><type>6</type><title>笔记.docx</title><appattach><aeskey>SECRET</aeskey></appattach></appmsg>'
    file_quote = quote(file_xml, 49)
    assert '[文件] 笔记.docx' in parse_message(file_quote, 49).text
    value = parse_message(quote(file_quote, 49), 49).text
    assert '[文件] 笔记.docx' in value and 'aeskey' not in value


def test_literal_text_and_xml_sent_as_text_are_preserved():
    content = '普通文字 & <img aeskey="用户自己输入的文字"/>'
    assert ('引用 测试用户：' + content) in parse_message(quote(content, 1), 49).text
    assert parse_message(content, 1).text == content


def test_double_escaped_missing_type_and_actual_child_xml():
    assert '[图片]' in parse_message(quote(escape(IMAGE_XML), 0), 49).text
    assert '[图片]' in parse_message(quote('<msg><img/></msg>', 3, 'child'), 49).text


def test_damaged_structured_reference_never_dumps_raw_fields():
    result = parse_message(quote('<msg><appmsg><aeskey>SECRET', 49), 49)
    assert '暂未解析' in result.text and 'SECRET' not in result.text
    result = parse_message(quote('<!DOCTYPE x [<!ENTITY a "SECRET">]><msg>&a;</msg>', 49), 49)
    assert 'SECRET' not in result.text


def test_deep_reference_chain_is_bounded():
    value = '文字'
    for _ in range(9):
        value = quote(value, 49)
    result = parse_message(value, 49)
    assert '嵌套消息' in result.text and len(result.text) < 500


def test_forwarded_attachments_and_text():
    record = ('<recordinfo><datalist><dataitem datatype="2"><sourcename>甲</sourcename>'
              '<datadesc>' + escape(IMAGE_XML) + '</datadesc></dataitem>'
              '<dataitem datatype="8"><sourcename>乙</sourcename><datatitle>附件.pdf</datatitle></dataitem>'
              '<dataitem datatype="1"><sourcename>丙</sourcename><datadesc>正常文字</datadesc></dataitem>'
              '</datalist></recordinfo>')
    raw = '<msg><appmsg><type>19</type><title>聊天记录</title><recorditem>' + escape(record) + '</recorditem></appmsg></msg>'
    value = parse_message(raw, 49).text
    assert '甲：[图片]' in value and '乙：[文件] 附件.pdf' in value and '丙：正常文字' in value
    assert 'SYNTHETIC_SECRET' not in value


def test_old_archive_docx_is_fixed_without_reimport(tmp_path):
    archive = archive_fixture(tmp_path)
    with closing(sqlite3.connect(archive.path)) as con:
        con.execute("UPDATE messages SET kind='引用',text=?,raw=? WHERE id=(SELECT min(id) FROM messages WHERE conversation=?)",
                    ('旧版错误：' + IMAGE_XML, quote(IMAGE_XML), FRIEND))
        con.commit()
    before = archive.path.read_bytes()
    result = export_docx(archive, FRIEND, tmp_path / 'fixed.docx')
    with zipfile.ZipFile(result.path) as doc:
        xml = doc.read('word/document.xml').decode()
        assert '引用 测试用户：[图片]' in xml
        assert 'SYNTHETIC_SECRET' not in xml and 'INTERNAL_CDN' not in xml
    assert archive.path.read_bytes() == before
    assert xml.count('<wp:inline') == 2
    assert '引用图片 · 测试用户' in xml
    assert result.pictures == 2 and result.missing == 0


def test_missing_reference_does_not_use_other_conversation_photo(tmp_path):
    archive = archive_fixture(tmp_path)
    with closing(sqlite3.connect(archive.path)) as con:
        con.execute("UPDATE messages SET server_id='999' WHERE kind='图片'")
        con.execute("UPDATE messages SET kind='引用',text='',raw=? WHERE id=(SELECT min(id) FROM messages WHERE conversation=?)",
                    (quote(IMAGE_XML, server_id='999'), 'test_room@chatroom'))
        con.commit()
    result = export_docx(archive, 'test_room@chatroom', tmp_path / 'unmatched.docx')
    assert result.pictures == 0 and result.missing == 1
    with zipfile.ZipFile(result.path) as doc:
        assert '引用图片未导出' in doc.read('word/document.xml').decode()
        assert not any(name.startswith('word/media/') for name in doc.namelist())


def test_nested_image_reference_is_extracted():
    refs = quoted_images(quote(quote(IMAGE_XML), 49))
    assert refs == [{'server_id': '102', 'sender': '测试用户'}]
