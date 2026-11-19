from unittest import TestCase
from unittest.mock import patch
from html import escape

from tests.test_document_import import OFFICE, W, package, paragraph
from app.document_import import DocumentImportError, parse_document


B = chr(96)


def run(text, properties=''):
    return '<w:r>' + ('<w:rPr>' + properties + '</w:rPr>' if properties else '') + '<w:t xml:space="preserve">' + escape(text) + '</w:t></w:r>'


def list_item(text, identity='7', level=0, style=None):
    props = '<w:pStyle w:val="' + style + '"/>' if style else ''
    if identity is not None:
        props += '<w:numPr><w:ilvl w:val="' + str(level) + '"/><w:numId w:val="' + identity + '"/></w:numPr>'
    return '<w:p><w:pPr>' + props + '</w:pPr>' + run(text) + '</w:p>'


def numbered_package(body, levels=None, instances=None, styles=None):
    if levels is None:
        levels = ('<w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
                  '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%2."/></w:lvl>')
    if instances is None:
        instances = '<w:num w:numId="7"><w:abstractNumId w:val="1"/></w:num>'
    numbering = '<w:numbering xmlns:w="' + W + '"><w:abstractNum w:abstractNumId="1">' + levels + '</w:abstractNum>' + instances + '</w:numbering>'
    extras = {'word/numbering.xml': numbering}
    relationships = '<Relationship Id="n" Type="' + OFFICE + 'numbering" Target="numbering.xml"/>'
    if styles:
        extras['word/styles.xml'] = '<w:styles xmlns:w="' + W + '">' + styles + '</w:styles>'
        relationships += '<Relationship Id="s" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
    return package(body, extras, relationships)


def linked_package(target, text='链接 [文字]'):
    rel = '<Relationship Id="link" Type="' + OFFICE + 'hyperlink" Target="' + escape(target, quote=True) + '" TargetMode="External"/>'
    body = '<w:p><w:hyperlink xmlns:r="' + OFFICE.rstrip('/') + '" r:id="link">' + run(text) + '</w:hyperlink></w:p>'
    return package(body, relationships=rel)


def rich_fixture():
    styles = ('<w:styles xmlns:w="' + W + '">'
              '<w:style w:type="character" w:styleId="CodeChar"><w:name w:val="Inline Code"/></w:style>'
              '</w:styles>')
    numbering = ('<w:numbering xmlns:w="' + W + '"><w:abstractNum w:abstractNumId="1">'
                 '<w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
                 '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl>'
                 '</w:abstractNum><w:num w:numId="7"><w:abstractNumId w:val="1"/></w:num></w:numbering>')
    def item(text, level=0):
        return ('<w:p><w:pPr><w:numPr><w:ilvl w:val="' + str(level)
                + '"/><w:numId w:val="7"/></w:numPr></w:pPr><w:r><w:t>' + text + '</w:t></w:r></w:p>')
    body = (paragraph('转换示例', 'Heading1')
            + '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">加粗文本 </w:t></w:r>'
              '<w:r><w:rPr><w:i/></w:rPr><w:t>斜体</w:t></w:r><w:r><w:t>，</w:t></w:r>'
              '<w:r><w:rPr><w:strike/></w:rPr><w:t>删除线</w:t></w:r><w:r><w:t>，</w:t></w:r>'
              '<w:r><w:rPr><w:rStyle w:val="CodeChar"/></w:rPr><w:t>x = 2 * 3</w:t></w:r></w:p>'
            + '<w:p><w:hyperlink xmlns:r="' + OFFICE.rstrip('/') + '" r:id="link">'
              '<w:r><w:t>项目说明</w:t></w:r></w:hyperlink></w:p>'
            + item('第三项') + item('嵌套项目', 1) + item('第四项')
            + '<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc>' + paragraph('项目')
            + '</w:tc><w:tc>' + paragraph('结果') + '</w:tc></w:tr><w:tr><w:tc>'
            + paragraph('A | B') + '</w:tc><w:tc><w:p><w:r><w:t>第一行</w:t><w:br/><w:t>第二行</w:t></w:r></w:p>'
              '</w:tc></w:tr></w:tbl>' + paragraph('真实字面量 **文本 **，不能丢失星号。'))
    relationships = ('<Relationship Id="styles" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
                     '<Relationship Id="numbering" Type="' + OFFICE + 'numbering" Target="numbering.xml"/>'
                     '<Relationship Id="link" Type="' + OFFICE
                     + 'hyperlink" Target="https://example.org/guide?q=markdown" TargetMode="External"/>')
    return package(body, {'word/styles.xml': styles, 'word/numbering.xml': numbering}, relationships)


class RichDocumentImportTests(TestCase):
    def test_representative_rich_document(self):
        result = parse_document('rich.docx', rich_fixture())
        self.assertEqual(result['content'], '# 转换示例\n\n**加粗文本** *斜体*，~~删除线~~，' + B + 'x = 2 * 3' + B
                         + '\n\n[项目说明](<https://example.org/guide?q=markdown>)\n\n3. 第三项\n\n    - 嵌套项目\n\n4. 第四项'
                         + '\n\n| 项目 | 结果 |\n| --- | --- |\n| A \\| B | 第一行<br>第二行 |'
                         + '\n\n真实字面量 \\*\\*文本 \\*\\*，不能丢失星号。')
        self.assertFalse(any('编号未重建' in item or '只保留显示文字' in item for item in result['warnings']))

    def test_run_edges_adjacent_runs_and_combined_marks(self):
        body = '<w:p>' + run('  前', '<w:b/>') + run('后  ', '<w:b/>') + run('斜粗删', '<w:b/><w:i/><w:strike/>') + '</w:p>'
        self.assertEqual(parse_document('a.docx', package(body))['content'], '**前后**  ~~***斜粗删***~~')

    def test_character_and_paragraph_style_inheritance_with_direct_off(self):
        styles = ('<w:styles xmlns:w="' + W + '">'
                  '<w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:b/></w:rPr></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Derived"><w:basedOn w:val="Base"/></w:style>'
                  '<w:style w:type="character" w:styleId="Emphasis"><w:rPr><w:i/></w:rPr></w:style>'
                  '<w:style w:type="character" w:styleId="More"><w:basedOn w:val="Emphasis"/></w:style></w:styles>')
        body = '<w:p><w:pPr><w:pStyle w:val="Derived"/></w:pPr>' + run('粗体 ') + run('两者 ', '<w:rStyle w:val="More"/>') + run('正文', '<w:b w:val="0"/>') + '</w:p>'
        rel = '<Relationship Id="s" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
        self.assertEqual(parse_document('a.docx', package(body, {'word/styles.xml': styles}, rel))['content'], '**粗体** ***两者*** 正文')

    def test_code_style_uses_sufficient_fence_and_retains_spaces(self):
        styles = '<w:styles xmlns:w="' + W + '"><w:style w:type="character" w:styleId="CodeChar"><w:name w:val="Inline Code"/></w:style></w:styles>'
        rel = '<Relationship Id="s" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
        for text, expected in [('x = 2 * 3', B + 'x = 2 * 3' + B), (B + 'x' + B, B * 2 + ' ' + B + 'x' + B + ' ' + B * 2),
                               (' a & b ', B + '  a & b  ' + B), ('**真实星号 **', B + '**真实星号 **' + B)]:
            with self.subTest(text=text):
                body = '<w:p>' + run(text, '<w:rStyle w:val="CodeChar"/>') + '</w:p>'
                self.assertEqual(parse_document('a.docx', package(body, {'word/styles.xml': styles}, rel))['content'], expected)

    def test_doc_default_marks_and_disabled_strike(self):
        styles = '<w:styles xmlns:w="' + W + '"><w:docDefaults><w:rPrDefault><w:rPr><w:i/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>'
        rel = '<Relationship Id="s" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
        body = '<w:p>' + run('默认斜体 ', '<w:strike w:val="false"/>') + run('直立', '<w:i w:val="off"/>') + '</w:p>'
        self.assertEqual(parse_document('a.docx', package(body, {'word/styles.xml': styles}, rel))['content'], '*默认斜体* 直立')

    def test_literal_markdown_in_mixed_prose_is_preserved(self):
        text = '正文 **文本 **，还有 _值_ 和 ' + B + '代码' + B + '，<标签> &amp;'
        expected = r'正文 \*\*文本 \*\*，还有 \_值\_ 和 ' + chr(92) + B + '代码' + chr(92) + B + r'，\<标签\> &amp;amp;'
        self.assertEqual(parse_document('a.docx', package('<w:p>' + run(text) + '</w:p>'))['content'], expected)

    def test_standalone_markdown_export_bold_paragraph_is_normalized(self):
        for text in ['**方案超出了预算，如何调整？ **', '__方案超出了预算，如何调整？ __']:
            with self.subTest(text=text):
                result = parse_document('a.docx', package(paragraph(text)))
                self.assertEqual(result['content'], '**方案超出了预算，如何调整？**')
                self.assertTrue(any('独立纯文本段落' in item for item in result['warnings']))
        body = '<w:p>' + run('**方案超出了预算，') + run('如何调整？ **') + '</w:p>'
        self.assertEqual(parse_document('a.docx', package(body))['content'], '**方案超出了预算，如何调整？**')

    def test_standalone_compatibility_does_not_rewrite_code_or_mixed_markup(self):
        for text in ['***星号***', '**内部 **粗体** 标记**', '**带 ' + B + '代码' + B + ' **']:
            with self.subTest(text=text):
                result = parse_document('a.docx', package('<w:p>' + run(text) + '</w:p>'))
                self.assertFalse(any('独立纯文本段落' in item for item in result['warnings']))
                self.assertIn('\\*', result['content'])

    def test_safe_external_links_are_preserved_without_network(self):
        with patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            for target, expected in [('https://example.org/a (b)?q=x|y', 'https://example.org/a%20(b)?q=x%7Cy'),
                                     ('http://example.org/doc', 'http://example.org/doc'), ('mailto:a@example.org', 'mailto:a@example.org')]:
                with self.subTest(target=target):
                    result = parse_document('a.docx', linked_package(target))
                    self.assertEqual(result['content'], r'[链接 \[文字\]](<' + expected + '>)')

    def test_unsafe_external_links_keep_only_displayed_text(self):
        for target in ['javascript:alert(1)', 'data:text/html,test', 'file:///C:/x', 'ftp://example.org/x',
                       'https://user:password@example.org', 'https://example.org:99999', 'https://[bad',
                       'mailto:a@example.org%0D%0ABcc:b@example.org', 'https://example.org/\\bad']:
            with self.subTest(target=target), patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
                result = parse_document('a.docx', linked_package(target, '显示文字'))
                self.assertEqual(result['content'], '显示文字')
                self.assertTrue(any('不安全' in item for item in result['warnings']))

    def test_ordered_start_nested_restart_and_separate_instances(self):
        body = list_item('上级') + list_item('子一', level=1) + list_item('子二', level=1) + list_item('下一上级') + list_item('重启子项', level=1)
        self.assertEqual(parse_document('a.docx', numbered_package(body))['content'], '3. 上级\n\n    1. 子一\n\n    2. 子二\n\n4. 下一上级\n\n    1. 重启子项')

    def test_list_start_override_and_explicit_never_restart(self):
        levels = ('<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl>'
                  '<w:lvl w:ilvl="1"><w:start w:val="2"/><w:numFmt w:val="decimal"/><w:lvlRestart w:val="0"/></w:lvl>')
        instances = '<w:num w:numId="7"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="7"/></w:lvlOverride></w:num>'
        body = list_item('上') + list_item('子', level=1) + list_item('下') + list_item('续', level=1)
        self.assertEqual(parse_document('a.docx', numbered_package(body, levels, instances))['content'], '7. 上\n\n    2. 子\n\n8. 下\n\n    3. 续')

    def test_bullet_list_and_missing_parent_level_do_not_become_code(self):
        levels = '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/></w:lvl>'
        self.assertEqual(parse_document('a.docx', numbered_package(list_item('项目', level=1), levels))['content'], '- 项目')

    def test_numbered_paragraph_style_and_direct_zero_suppression(self):
        styles = '<w:style w:type="paragraph" w:styleId="Numbered"><w:pPr><w:numPr><w:numId w:val="7"/></w:numPr></w:pPr></w:style>'
        body = list_item('样式编号', identity=None, style='Numbered') + list_item('取消编号', identity='0', style='Numbered')
        self.assertEqual(parse_document('a.docx', numbered_package(body, styles=styles))['content'], '3. 样式编号\n\n取消编号')

    def test_numbering_level_links_paragraph_style(self):
        levels = '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:pStyle w:val="Linked"/></w:lvl>'
        self.assertEqual(parse_document('a.docx', numbered_package(paragraph('链接样式', 'Linked'), levels))['content'], '- 链接样式')

    def test_separate_numbering_instances_have_independent_counters(self):
        instances = ''.join('<w:num w:numId="' + identity + '"><w:abstractNumId w:val="1"/></w:num>' for identity in ['7', '8'])
        body = list_item('A', '7') + list_item('B', '8') + list_item('C', '7')
        self.assertEqual(parse_document('a.docx', numbered_package(body, instances=instances))['content'], '3. A\n\n3. B\n\n4. C')

    def test_unsupported_or_missing_numbering_keeps_text_and_warns(self):
        for payload in [package(list_item('正文')), numbered_package(list_item('正文'), '<w:lvl w:ilvl="0"><w:numFmt w:val="ordinalText"/></w:lvl>')]:
            result = parse_document('a.docx', payload)
            self.assertEqual(result['content'], '正文')
            self.assertTrue(any('列表' in item for item in result['warnings']))

    def test_invalid_numbering_and_numbering_dtd_are_rejected(self):
        rel = '<Relationship Id="n" Type="' + OFFICE + 'numbering" Target="numbering.xml"/>'
        for xml in ['<!DOCTYPE x [<!ENTITY y "x">]><w:numbering xmlns:w="' + W + '"/>',
                    '<w:numbering xmlns:w="' + W + '"><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="999"/></w:abstractNum></w:numbering>']:
            with self.subTest(xml=xml), self.assertRaises(DocumentImportError):
                parse_document('a.docx', package(paragraph('正文'), {'word/numbering.xml': xml}, rel))

    def test_invalid_styles_cycle_and_styles_dtd_are_rejected(self):
        rel = '<Relationship Id="s" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
        for xml in ['<!DOCTYPE x><w:styles xmlns:w="' + W + '"/>',
                    '<w:styles xmlns:w="' + W + '"><w:style w:type="paragraph" w:styleId="Cycle"><w:basedOn w:val="Cycle"/></w:style></w:styles>']:
            with self.subTest(xml=xml), self.assertRaises(DocumentImportError):
                parse_document('a.docx', package(paragraph('正文'), {'word/styles.xml': xml}, rel))

    def test_table_escapes_pipes_and_keeps_breaks_empty_cells_and_formatting(self):
        body = ('<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc>' + paragraph('A') + '</w:tc><w:tc>' + paragraph('B')
                + '</w:tc></w:tr><w:tr><w:tc><w:p>' + run('a|b', '<w:b/>') + '<w:r><w:br/><w:t>后行</w:t></w:r></w:p>'
                + paragraph('第二段') + '</w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>')
        self.assertEqual(parse_document('a.docx', package(body))['content'], '| A | B |\n| --- | --- |\n| **a\\|b**<br>后行<br><br>第二段 |  |')

    def test_merged_and_irregular_tables_keep_text_with_explicit_warning(self):
        for props in ['<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>', '<w:tcPr><w:vMerge w:val="restart"/></w:tcPr>']:
            body = '<w:tbl><w:tr><w:tc>' + props + paragraph('合并内容') + '</w:tc></w:tr></w:tbl>'
            result = parse_document('a.docx', package(body))
            self.assertEqual(result['content'], '合并内容')
            self.assertTrue(any('合并' in item for item in result['warnings']))
        body = '<w:tbl><w:tr><w:tc>' + paragraph('A') + '</w:tc><w:tc>' + paragraph('B') + '</w:tc></w:tr><w:tr><w:tc>' + paragraph('C') + '</w:tc></w:tr></w:tbl>'
        self.assertEqual(parse_document('a.docx', package(body))['content'], 'A\tB\n\nC')

    def test_empty_or_image_only_table_does_not_invent_content(self):
        body = '<w:tbl><w:tr><w:tc><w:p><w:r><w:drawing/></w:r></w:p></w:tc></w:tr></w:tbl>'
        with self.assertRaisesRegex(DocumentImportError, 'OCR'):
            parse_document('a.docx', package(body))
