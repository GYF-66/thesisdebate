from io import BytesIO
import unittest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from app.pdf_import import extract_pdf, heading_level


def make_pdf(pages):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    output = BytesIO(); pdf = canvas.Canvas(output)
    for rows in pages:
        for index, (text, size) in enumerate(rows):
            pdf.setFont('STSong-Light', size); pdf.drawString(72, 780 - index * 28, text)
        pdf.showPage()
    pdf.save(); return output.getvalue()


class PdfOutlineTests(unittest.TestCase):
    def test_numbered_heading_levels(self):
        for title, level in [('第一章 绪论', 1), ('第二节 研究背景', 2), ('1.2研究方法', 2), ('１．２．３实验设计', 3), ('Chapter 2 Methods', 1), ('Section 2.1 Results', 2)]:
            with self.subTest(title=title): self.assertEqual(heading_level(title), level)

    def test_avoid_table_of_contents_dates_and_sentences(self):
        for text in ['第一章 绪论 ........ 1', '1.2 研究方法……12', '2026 年研究报告', '1.2 表示结果的数值。']:
            with self.subTest(text=text): self.assertIsNone(heading_level(text))

    def test_pdf_chapters_keep_hierarchy_and_body(self):
        result = extract_pdf(make_pdf([[('第一章 绪论', 18), ('正文包含 38 份有效样本。', 12), ('第一节 研究背景', 16), ('1.1.1研究目标', 14)], [('第二章 研究方法', 18), ('这里是第二章的正文。', 12)]]))
        self.assertIn('# 第一章 绪论', result['content'])
        self.assertIn('## 第一节 研究背景', result['content'])
        self.assertIn('### 1.1.1研究目标', result['content'])
        self.assertIn('# 第二章 研究方法', result['content'])
        self.assertTrue(any('4 个章节标题' in w for w in result['warnings']))

    def test_unnumbered_large_headings(self):
        result = extract_pdf(make_pdf([[('研究设计', 18), ('这里是论文正文，包含 38 份有效样本，说明研究过程。', 12), ('实验方法', 16), ('这里是方法的详细正文，说明具体步骤和研究局限。', 12)]]))
        self.assertIn('# 研究设计', result['content']); self.assertIn('## 实验方法', result['content'])
        self.assertNotIn('# 这里是', result['content'])

    def test_real_bookmark_titles_and_levels_only(self):
        writer = PdfWriter(); writer.append(PdfReader(BytesIO(make_pdf([[('研究总览', 12), ('实验步骤', 12), ('真实正文记录了 38 份有效样本。', 12)]]))))
        parent = writer.add_outline_item('研究总览', 0); writer.add_outline_item('实验步骤', 0, parent=parent)
        writer.add_outline_item('书签中不存在于正文的标题', 0)
        output=BytesIO(); writer.write(output); result=extract_pdf(output.getvalue())
        self.assertIn('# 研究总览', result['content']); self.assertIn('## 实验步骤', result['content'])
        self.assertNotIn('书签中不存在于正文的标题', result['content'])

    def test_toc_rows_and_repeated_running_headers_are_not_duplicate_headings(self):
        result=extract_pdf(make_pdf([[('第一章 绪论 .... 1', 12), ('第一章 绪论', 18), ('正文记录了 38 份有效样本。', 12)], [('第一章 绪论', 10), ('第二页仍然是本章正文，包含样本数据和结论。', 12), ('第二章 研究方法', 18)]]))
        self.assertEqual(result['content'].count('# 第一章 绪论'), 1)
        self.assertIn('第一章 绪论 .... 1', result['content'])
        self.assertIn('# 第二章 研究方法', result['content'])

    def test_same_size_plain_body_is_not_invented_as_headings(self):
        result=extract_pdf(make_pdf([[('这是没有章节标记的连续正文。', 12), ('38 份有效样本', 12), ('继续记录研究过程。', 12)]]))
        self.assertFalse(any(line.startswith('#') for line in result['content'].splitlines()))
        self.assertTrue(any('未识别到' in w for w in result['warnings']))


if __name__ == '__main__': unittest.main()
