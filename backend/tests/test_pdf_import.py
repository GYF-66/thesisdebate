from io import BytesIO
import unittest

from docx import Document
from docx.shared import Pt
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.document_import import parse_document, DocumentImportError
from app.school_templates import import_template


def pdf_fixture(spec=False):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    output = BytesIO()
    page = canvas.Canvas(output)
    page.setFont('STSong-Light', 18)
    page.drawString(72, 760, '第一章 研究设计')
    page.setFont('STSong-Light', 12)
    page.drawString(72, 720, '这是可搜索的中文论文，样本数量为 38。')
    page.drawString(72, 690, '1.1 研究方法')
    page.drawString(72, 660, '通过问卷分析学习体验。')
    if spec:
        for y, line in zip(range(620, 440, -30), (
            '正文：宋体，小四，1.5 倍行距', '一级标题：黑体，三号',
            '上边距 3 厘米，下边距 2.5 厘米', '左边距 30 毫米，右边距 25 毫米',
            '首行缩进 2 字符',
        )):
            page.drawString(72, y, line)
    page.save()
    return output.getvalue()


class PdfImportTests(unittest.TestCase):
    def test_real_chinese_pdf_text_and_headings(self):
        data = pdf_fixture()
        parsed = parse_document('论文.pdf', data)
        self.assertEqual(parsed['source_type'], 'pdf')
        self.assertIn('# 第一章 研究设计', parsed['content'])
        self.assertIn('## 1.1 研究方法', parsed['content'])
        self.assertIn('样本数量为 38', parsed['content'])

    def test_pdf_template_numbering_font_size_and_explicit_requirements(self):
        template = import_template('大学规范.pdf', pdf_fixture(spec=True))
        self.assertEqual(template.heading_numbering[:2], ['chapter', 'decimal'])
        self.assertEqual(template.document_style.font_family, '宋体')
        self.assertEqual(template.document_style.font_size_pt, 12)
        self.assertEqual(template.document_style.headings[0].font_family, '黑体')
        self.assertEqual(template.document_style.headings[0].font_size_pt, 16)
        self.assertEqual(template.document_style.margin_top_mm, 30)
        self.assertEqual(template.document_style.margin_bottom_mm, 25)
        self.assertEqual(template.document_style.margin_left_mm, 30)
        self.assertEqual(template.document_style.margin_right_mm, 25)
        self.assertEqual(template.document_style.first_line_indent_pt, 24)
        self.assertTrue(any('默认值' in warning for warning in template.warnings))

    def test_pdf_font_only_template_reports_uncertain_fields(self):
        template = import_template('论文样例.PDF', pdf_fixture())
        self.assertEqual(template.document_style.font_size_pt, 12)
        self.assertEqual(template.document_style.headings[0].font_size_pt, 18)
        self.assertTrue(template.warnings)

    def test_encrypted_pdf_is_actionable(self):
        writer = PdfWriter()
        writer.append(PdfReader(BytesIO(pdf_fixture())))
        writer.encrypt('fixture-secret')
        output = BytesIO(); writer.write(output)
        with self.assertRaisesRegex(DocumentImportError, '加密'):
            parse_document('加密.pdf', output.getvalue())

    def test_blank_pdf_is_not_reported_as_success(self):
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842)
        output = BytesIO(); writer.write(output)
        with self.assertRaisesRegex(DocumentImportError, 'OCR'):
            parse_document('扫描页.pdf', output.getvalue())

    def test_invalid_pdf_is_not_reported_as_success(self):
        with self.assertRaisesRegex(DocumentImportError, 'PDF'):
            parse_document('坏文件.pdf', b'%PDF-1.4 invalid')

    def test_word_styles_without_example_body_can_be_imported_as_template(self):
        doc = Document()
        doc.styles['Normal'].font.name = '宋体'
        doc.styles['Normal'].font.size = Pt(14)
        output = BytesIO(); doc.save(output)
        draft = import_template('空白学校模板.docx', output.getvalue())
        self.assertEqual(draft.document_style.font_size_pt, 14)

    def test_word_body_import_remains_real_content(self):
        doc = Document(); doc.add_heading('研究方法', 1); doc.add_paragraph('测试样本 38 份')
        output = BytesIO(); doc.save(output)
        content = parse_document('论文.docx', output.getvalue())['content']
        self.assertIn('# **研究方法**', content)
        self.assertIn('测试样本 38 份', content)


if __name__ == '__main__':
    unittest.main()
