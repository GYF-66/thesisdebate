"""Real storage, scan freshness, export replay and PDF thumbnail regressions."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import unittest
from fastapi import HTTPException
from PIL import Image
from app import agent_api, main, workbench_api as api
from app.storage import ReviewStore
from app.document_export import DocumentExportRequest, download_document
from app.document_layout import DocumentStyle


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)/'workspace.sqlite3'
        self.previous_store = agent_api.STORE
        self.previous_state = [dict(x) for x in (main.RUNS, main.CHANGES, main.SOURCES)]
        agent_api.STORE = ReviewStore(self.path)
        for state in (main.RUNS, main.CHANGES, main.SOURCES): state.clear()
        self.document = agent_api.STORE.create_workspace_document('研究设计.md', '# 标题\n\n38 份有效样本  \n')

    def tearDown(self):
        agent_api.STORE.close()
        agent_api.STORE = self.previous_store
        for state, previous in zip((main.RUNS, main.CHANGES, main.SOURCES), self.previous_state):
            state.clear(); state.update(previous)
        self.temp.cleanup()

    def reopen(self):
        agent_api.STORE.close(); agent_api.STORE = ReviewStore(self.path)

    def test_rule_selection_persists_and_empty_selection_scans_no_changes(self):
        original = api.read_rule_selection('generic-thesis')['enabled']
        self.assertTrue(original)
        api.save_rule_selection('generic-thesis', api.RuleSelection(enabled=[]))
        self.reopen()
        self.assertEqual(api.read_rule_selection('generic-thesis')['enabled'], [])
        run = main.create_format_run(main.FormatRunCreate(content=self.document['content'], document_type='markdown', profile_id='generic-thesis', requested_rules=[]))
        self.assertEqual(main.list_changes(run.id), [])
        with self.assertRaises(HTTPException) as caught:
            api.save_rule_selection('generic-thesis', api.RuleSelection(enabled=['not-a-rule']))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(api.read_rule_selection('generic-thesis')['enabled'], [])

    def test_statistics_track_real_scan_and_invalidate_edited_body(self):
        doc = self.document
        self.assertEqual(api.workspace_statistics()['scanned'], 0)
        run = main.create_format_run(main.FormatRunCreate(document_id=doc['id'], content=doc['content'], document_type='markdown', profile_id='generic-thesis'))
        doc = agent_api.STORE.save_workspace_document(doc['id'],doc['name'],doc['content'],None,doc['revision'],run.id,True)
        result = api.workspace_statistics()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(result['pending'], len(main.list_changes(run.id)))
        self.assertTrue(api.rule_selection_status(run.id)['matches'])
        defaults = api.read_rule_selection('generic-thesis')['enabled']
        api.save_rule_selection('generic-thesis',api.RuleSelection(enabled=[]))
        self.assertFalse(api.rule_selection_status(run.id)['matches'])
        self.assertEqual(api.workspace_statistics()['scanned'],0)
        api.save_rule_selection('generic-thesis',api.RuleSelection(enabled=defaults))
        agent_api.STORE.save_workspace_document(doc['id'],doc['name'],doc['content']+'新段落',None,doc['revision'])
        result = api.workspace_statistics()
        self.assertEqual(result['scanned'], 0)
        self.assertEqual(result['pending'], 0)
        self.assertEqual(result['documents'][0]['scan_status'], 'stale')

    def test_preview_is_not_history_and_explicit_exports_survive_restart(self):
        doc = self.document
        download_document(DocumentExportRequest(name=doc['name'],content=doc['content'],format='pdf',style=DocumentStyle()))
        self.assertEqual(api.exports(), [])
        expected = {}
        for fmt in ['md','docx','pdf']:
            response = download_document(DocumentExportRequest(name=doc['name'],content=doc['content'],format=fmt,document_id=doc['id'],track_history=True,style=DocumentStyle()))
            record = api.exports()[0]
            expected[record['id']] = response.body
        self.reopen()
        self.assertEqual(len(api.exports()),3)
        for identity,data in expected.items():
            self.assertEqual(api.download_export(identity).body,data)
        zipped = api.download_exports(api.ExportSelection(ids=list(expected)))
        with ZipFile(BytesIO(zipped.body)) as archive:
            self.assertEqual([archive.read(name) for name in archive.namelist()],list(expected.values()))
        for action in [lambda:api.download_export('missing'),lambda:api.download_exports(api.ExportSelection(ids=['missing']))]:
            with self.assertRaises(HTTPException) as caught: action()
            self.assertEqual(caught.exception.status_code,404)

    def test_thumbnail_decodes_and_document_contains_real_rules(self):
        from pypdf import PdfReader
        pdf = api.profile_document('generic-thesis')
        text = ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf.body)).pages)
        self.assertIn('通用学术论文规范',text)
        png = api.profile_thumbnail('generic-thesis')
        with Image.open(BytesIO(png.body)) as image:
            self.assertEqual(image.format,'PNG'); self.assertGreater(image.width,400)
        with self.assertRaises(HTTPException): api.profile_thumbnail('missing')
