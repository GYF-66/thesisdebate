"""Exact requested font identities and explicit PDF substitutions, without installs."""
from io import BytesIO
import asyncio
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from pypdf import PdfReader

from app.agent_chat import ChatRequest, local_plan
from app.document_export import DocumentExportRequest, export_bytes, export_docx, export_pdf, router as export_router
from app.document_fonts import FontCatalogue, FontFace, COMMON_FONTS, _read_faces, document_fonts, font_key, get_font_catalogue, match_font_name, router as fonts_router
from app.document_layout import DocumentStyle
from app.school_templates import import_template
from app.template_rules import extract_rules


class FontIdentityTests(unittest.TestCase):
    def test_unknown_requirements_remain_valid_but_unsafe_names_do_not(self):
        for name in ('仿宋_GB2312', '楷体_GB2312', '华文仿宋', '新安装的学校字体', 'A Font (Regular)'):
            self.assertEqual(DocumentStyle(font_family=name).font_family, name)
        for name in ('', ' A', 'A ', 'A; color:red', '../font.ttf', 'A"', 'A\x00', 'A\x7f'):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                DocumentStyle(font_family=name)

    def test_exact_names_and_longest_aliases_do_not_silently_shortmatch(self):
        catalogue = FontCatalogue([])
        with patch('app.document_fonts.get_font_catalogue', return_value=catalogue):
            for name in ('仿宋_GB2312', '楷体_GB2312', '华文仿宋', '华文楷体', 'FangSong_GB2312', 'KaiTi_GB2312'):
                expected = {'FangSong_GB2312': '仿宋_GB2312', 'KaiTi_GB2312': '楷体_GB2312'}.get(name, name)
                with self.subTest(name=name):
                    self.assertEqual(match_font_name('正文改为' + name + '小四'), expected)
                    plan = local_plan(ChatRequest(document_id='font-test', message='正文改为' + name + '小四', style=DocumentStyle()))
                    self.assertEqual(plan.proposed_style.font_family, expected)
                    draft = extract_rules('正文字体：' + name + '，小四号', '学校规范.txt')
                    fonts = [source.value for source in draft.rule_sources if source.field == 'document_style.font_family']
                    self.assertEqual(fonts, [expected])
            self.assertIsNone(match_font_name('正文使用某未知仿宋字族小四'))
            self.assertEqual(match_font_name('正文字体：学校专用字体，小四'), '学校专用字体')

    def test_imported_docx_and_json_preserve_requested_names(self):
        for name in ('仿宋_GB2312', '楷体_GB2312', '华文仿宋', 'FangSong'):
            style = DocumentStyle(font_family=name)
            data = export_docx('中文论文正文。', style)
            document = Document(BytesIO(data))
            self.assertEqual(document.styles['Normal']._element.rPr.rFonts.get(qn('w:eastAsia')), name)
            draft = import_template('学校样例.docx', data)
            self.assertEqual(draft.document_style.font_family, name)
            payload = {'name': '学校', 'heading_numbering': ['preserve'] * 6, 'document_style': style.model_dump()}
            restored = import_template('学校.json', json.dumps(payload).encode())
            self.assertEqual(restored.document_style.font_family, name)

    def test_missing_gb_font_is_not_installed_through_windows_substitution(self):
        face = FontFace('仿宋', ('FangSong',), '/fixture/simfang.ttf', 0, 'Regular', frozenset(map(ord, '中文论文ABC')))
        catalogue = FontCatalogue([face], {'FangSong_GB2312': 'FangSong'})
        item = catalogue.item('仿宋_GB2312')
        self.assertFalse(item['installed']); self.assertFalse(item['pdf_available'])
        self.assertEqual(item['status'], 'substituted'); self.assertEqual(item['substitute'], '仿宋')
        self.assertIsNone(catalogue.face('仿宋_GB2312'))
        with patch('app.document_fonts.get_font_catalogue', return_value=catalogue):
            result = document_fonts(q='fangsong', limit=100)
        self.assertEqual({item['name'] for item in result['fonts']}, {'仿宋', '仿宋_GB2312'})
        self.assertEqual(len(COMMON_FONTS), 9)
        self.assertNotIn('/fixture/', json.dumps(result))

    def test_catalogue_deduplicates_after_aliases_resolve_to_output_names(self):
        # Installed font metadata can expose a family once under a compact or
        # style name and again as an alias of its eventual display family.
        coverage = frozenset(map(ord, '中文论文ABC'))
        faces = [
            FontFace('FangSong Regular', (), '/fixture/fang-reg.ttf', 0, 'Regular', coverage),
            FontFace('仿宋', ('FangSong', 'FangSong Regular'), '/fixture/simfang.ttf', 0, 'Regular', coverage),
            FontFace('MicrosoftJhengHeiUILight', (), '/fixture/jheng-alias.ttf', 0, 'Light', coverage),
            FontFace('Microsoft JhengHei UI Light', ('MicrosoftJhengHeiUILight',), '/fixture/jheng.ttf', 0, 'Light', coverage),
        ]
        catalogue = FontCatalogue(faces)
        entries = catalogue.entries()
        self.assertEqual(len(entries), len({font_key(item['name']) for item in entries}))
        self.assertEqual([item['name'] for item in entries if item['common']], list(COMMON_FONTS))
        self.assertEqual([item['name'] for item in entries].count('仿宋'), 1)
        self.assertEqual([item['name'] for item in entries].count('Microsoft JhengHei UI Light'), 1)
        with patch('app.document_fonts.get_font_catalogue', return_value=catalogue):
            response = document_fonts(q='MicrosoftJhengHeiUILight', limit=100)
        self.assertEqual(response['total'], 1)
        self.assertEqual(response['fonts'][0]['name'], 'Microsoft JhengHei UI Light')
        self.assertIn('MicrosoftJhengHeiUILight', response['fonts'][0]['aliases'])

    def test_installed_catalogue_output_names_are_unique_after_canonicalization(self):
        entries = get_font_catalogue().entries()
        keys = [font_key(item['name']) for item in entries]
        self.assertEqual(len(keys), len(set(keys)), 'Real installed aliases must not create duplicate display families.')

    def test_pdf_missing_or_unsupported_font_needs_specific_confirmation(self):
        with patch('app.document_export.get_font_catalogue', return_value=FontCatalogue([])):
            with self.assertRaises(HTTPException) as raised:
                export_pdf('中文论文', DocumentStyle(font_family='仿宋_GB2312'))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail['code'], 'font_confirmation_required')
        self.assertEqual(raised.exception.detail['issues'][0]['requested_font'], '仿宋_GB2312')
        self.assertIsNone(raised.exception.detail['issues'][0]['suggested_font'])

    def test_font_file_metadata_includes_each_ttc_face_and_glyph_coverage(self):
        path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/simsun.ttc'
        if not path.is_file():
            self.skipTest('Windows SimSun TTC is not installed on this host')
        faces = _read_faces(path)
        self.assertGreaterEqual(len(faces), 2)
        self.assertEqual({face.index for face in faces}, set(range(len(faces))))
        self.assertTrue(all(ord('中') in face.coverage for face in faces))
        self.assertIn('宋体', {face.family for face in faces})
        self.assertIn('新宋体', {face.family for face in faces})

    def test_actual_missing_font_confirmation_preserves_style_and_embeds_replacement(self):
        catalogue = get_font_catalogue()
        replacement = catalogue.face('仿宋', '中文论文 Aa 123')
        if not replacement or replacement.pdf_reason:
            self.skipTest('Exact embeddable FangSong is not installed on this host')
        style = DocumentStyle(font_family='Definitely Missing Font 20261119')
        before = style.model_dump()
        with self.assertRaises(HTTPException) as raised:
            export_pdf('中文论文 Aa 123', style)
        self.assertEqual(raised.exception.status_code, 409)
        warnings = []
        data = export_pdf('中文论文 Aa 123', style, warnings, {style.font_family: '仿宋'})
        self.assertIn('中文论文', PdfReader(BytesIO(data)).pages[0].extract_text())
        self.assertEqual(style.model_dump(), before)
        self.assertTrue(any('明确确认' in item for item in warnings))

    def test_missing_font_name_is_still_writable_to_docx_without_substitution(self):
        style = DocumentStyle(font_family='仿宋_GB2312')
        payload = DocumentExportRequest(content='中文论文', style=style, format='docx')
        data, _, _, _ = export_bytes(payload)
        self.assertEqual(Document(BytesIO(data)).styles['Normal'].font.name, '仿宋_GB2312')

    def test_unsupported_outline_and_missing_glyphs_are_not_pdf_available(self):
        from app.document_export import _font_problem
        unavailable = FontFace('Only CFF', (), '/fixture/a.otf', 0, 'Regular', frozenset(map(ord, 'ABC')), '当前PDF引擎不支持此字体轮廓格式')
        partial = FontFace('Latin Only', (), '/fixture/b.ttf', 0, 'Regular', frozenset(map(ord, 'ABC')))
        catalogue = FontCatalogue([unavailable, partial])
        self.assertFalse(catalogue.item('Only CFF')['pdf_available'])
        with patch('app.document_export.get_font_catalogue', return_value=catalogue):
            self.assertIn('缺少字符', _font_problem('Latin Only', '中文')[0])
            self.assertIn('轮廓格式', _font_problem('Only CFF', 'ABC')[0])

    def test_real_http_catalogue_and_confirmed_pdf_download(self):
        from tests.test_document_layout_exports import request
        catalogue = get_font_catalogue()
        if not catalogue.face('仿宋', '中文论文 Aa 123'):
            self.skipTest('FangSong is not installed on this host')
        app = FastAPI(); app.include_router(fonts_router); app.include_router(export_router)
        status, data, _ = asyncio.run(request(app, 'GET', '/document-fonts'))
        self.assertEqual(status, 200)
        entries = json.loads(data)['fonts']
        self.assertEqual(len(entries), len({font_key(item['name']) for item in entries}))
        self.assertTrue(set(COMMON_FONTS) <= {item['name'] for item in entries})
        self.assertTrue(any(item['installed'] and item['name'] == '华文仿宋' for item in entries))
        self.assertNotIn('C:\\', data.decode())
        style = DocumentStyle(font_family='Definitely Missing Font 20261119')
        payload = {'content': '中文论文 Aa 123', 'style': style.model_dump(), 'format': 'pdf'}
        status, data, _ = asyncio.run(request(app, 'POST', '/document-export', payload))
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data)['detail']['code'], 'font_confirmation_required')
        payload['font_substitutions'] = {style.font_family: '仿宋'}
        status, data, headers = asyncio.run(request(app, 'POST', '/document-export', payload))
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertIn('中文论文', PdfReader(BytesIO(data)).pages[0].extract_text())
        self.assertIn(b'application/pdf', headers[b'content-type'])
        self.assertEqual(payload['style']['font_family'], 'Definitely Missing Font 20261119')


if __name__ == '__main__':
    unittest.main()
