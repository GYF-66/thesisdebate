from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from app.document_import import parse_document
from app.docx_preservation import retain_original, protected_export, compatibility
from app.storage import ReviewStore
from tests.test_document_import import package, paragraph


class SourceProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name) / 'db.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def retain(self, data):
        content = parse_document('sample.docx', data)['content']
        doc = self.store.create_workspace_document('sample.docx', content)
        retain_original(self.store, doc['id'], 'sample.docx', data, content)
        return doc['id'], content

    def test_unchanged_returns_original_exact_bytes(self):
        data = package(paragraph('完整保留的原始正文。'))
        doc_id, content = self.retain(data)
        result, warnings = protected_export(self.store, doc_id, content)
        self.assertEqual(result, data)
        self.assertTrue(warnings)
        self.assertTrue(compatibility(self.store, doc_id)['retained'])

    def test_multirun_local_change_keeps_all_other_parts_and_objects(self):
        body = ('<w:p><w:r><w:t>本研究的样本</w:t></w:r><w:r><w:t>已经核验。</w:t></w:r></w:p>'
                '<w:p><m:oMath><m:r><m:t>x=2</m:t></m:r></m:oMath></w:p>'
                '<w:p><w:r><w:drawing/></w:r></w:p>'
                '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:t>目录</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
                '<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>批注锚点</w:t></w:r><w:commentRangeEnd w:id="0"/></w:p>'
                '<w:ins w:id="1"><w:p><w:r><w:t>已有修订</w:t></w:r></w:p></w:ins><w:sectPr/>')
        data = package(body, extras={'word/media/image.png': b'fixture-png', 'word/comments.xml': '<comments/>', 'word/footnotes.xml': '<footnotes/>'})
        doc_id, content = self.retain(data)
        changed = content.replace('研究的样本已经', '项目样本均已')
        result, _ = protected_export(self.store, doc_id, changed)
        self.assertEqual(parse_document('sample.docx', result)['content'], changed)
        with ZipFile(BytesIO(data)) as original, ZipFile(BytesIO(result)) as updated:
            self.assertEqual(original.namelist(), updated.namelist())
            for name in original.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(original.read(name), updated.read(name))
            from lxml import etree
            a, b = [etree.fromstring(archive.read('word/document.xml')) for archive in (original, updated)]
            self.assertEqual([etree.tostring(item, method='c14n') for item in a[0][1:]], [etree.tostring(item, method='c14n') for item in b[0][1:]])

    def test_protected_structural_duplicate_and_markdown_changes_are_blocked(self):
        data = package(paragraph('重复段落') + paragraph('重复段落') + paragraph('独立正文') + '<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>已批注文字</w:t></w:r><w:commentRangeEnd w:id="0"/></w:p>')
        doc_id, content = self.retain(data)
        for changed in (content.replace('已批注文字', '新的内容'), content + '\n\n新增章节', content.replace('重复段落', '换字', 1), content.replace('独立正文', '**独立正文**')):
            with self.subTest(content=changed), self.assertRaises(ValueError):
                protected_export(self.store, doc_id, changed)

    def test_original_package_is_durable_after_store_reopen(self):
        data = package(paragraph('重启后仍能恢复。'))
        doc_id, content = self.retain(data)
        self.store.close()
        self.store = ReviewStore(Path(self.temp.name) / 'db.sqlite3')
        self.assertEqual(protected_export(self.store, doc_id, content)[0], data)

    def test_checksum_failure_never_silently_exports_a_copy(self):
        data = package(paragraph('校验中的正文'))
        doc_id, content = self.retain(data)
        with self.store._transaction() as connection:
            row = connection.execute("SELECT payload FROM records WHERE namespace='original_docx' AND record_id=?", (doc_id,)).fetchone()
            value = json.loads(row['payload']); value['sha256'] = '0' * 64
            connection.execute("UPDATE records SET payload=? WHERE namespace='original_docx' AND record_id=?", (json.dumps(value), doc_id))
        with self.assertRaisesRegex(ValueError, '校验失败'):
            protected_export(self.store, doc_id, content + '。')
