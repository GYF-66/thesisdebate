from io import BytesIO
import struct
import unittest
from unittest.mock import patch
import warnings
import zipfile

from app.document_import import (
    COPY_WARNING, DocumentImportError, MAX_CONTENT_CHARACTERS, MAX_UPLOAD_BYTES, parse_document,
)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def paragraph(text, style=None):
    props = '<w:pPr><w:pStyle w:val="' + style + '"/></w:pPr>' if style else ""
    return '<w:p>' + props + '<w:r><w:t xml:space="preserve">' + text + '</w:t></w:r></w:p>'


def package(body=None, extras=None, relationships="", main_type=MAIN_TYPE,
            document=None, root_relationships=None):
    if body is None:
        body = paragraph("真实论文正文。")
    if document is None:
        document = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="' + W
                    + '" xmlns:m="' + M + '"><w:body>' + body + '</w:body></w:document>')
    types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="' + main_type + '"/></Types>')
    rels = ('<Relationships xmlns="' + REL + '"><Relationship Id="main" Type="' + OFFICE
            + 'officeDocument" Target="word/document.xml"/></Relationships>')
    members = {"[Content_Types].xml": types, "_rels/.rels": root_relationships or rels,
               "word/document.xml": document}
    if relationships:
        members["word/_rels/document.xml.rels"] = '<Relationships xmlns="' + REL + '">' + relationships + '</Relationships>'
    members.update(extras or {})
    data = BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)
    return data.getvalue()


class DocumentImportTests(unittest.TestCase):
    def fail_import(self, name, data, text):
        with self.assertRaisesRegex(DocumentImportError, text):
            parse_document(name, data)

    def test_markdown_is_real_utf8_copy_without_content_rewrite(self):
        content = "# 自己的论文\r\n\r\n样本数为 38，均值为 2.75。  \r\n"
        result = parse_document("我的论文.md", content.encode("utf-8"))
        self.assertEqual(result, {"name": "我的论文.md", "content": content,
                                 "document_type": "markdown", "source_type": "markdown",
                                 "warnings": [COPY_WARNING]})

    def test_txt_bom_and_basename(self):
        result = parse_document(r"C:\fakepath\我的论文.TXT", bytes([239, 187, 191]) + "正文\n".encode())
        self.assertEqual(result["content"], "正文\n")
        self.assertEqual(result["source_type"], "text")
        self.assertEqual(result["name"], "我的论文.TXT")
        self.assertEqual(parse_document("a.markdown", b"# Test")["source_type"], "markdown")

    def test_invalid_encoding_and_binary_text(self):
        self.fail_import("a.txt", "中文".encode("gbk"), "UTF-8")
        self.fail_import("a.txt", bytes([80, 75, 3, 4, 0]) + b"abc", "二进制")

    def test_empty_and_whitespace_files(self):
        self.fail_import("a.md", b"", "为空")
        self.fail_import("a.md", b" \n\t", "没有")

    def test_filename_and_supported_formats(self):
        self.fail_import("", b"text", "文件名")
        self.fail_import("bad\n.md", b"text", "文件名")
        self.fail_import("x" * 256 + ".md", b"text", "文件名")
        self.fail_import("论文.pdf", b"%PDF-1.4", "PDF 文件损坏")
        self.fail_import("论文.docm", b"text", "只支持")

    def test_upload_and_character_limits(self):
        self.fail_import("a.docx", b"a" * (MAX_UPLOAD_BYTES + 1), "10 MiB")
        self.fail_import("a.txt", ("中" * (MAX_CONTENT_CHARACTERS + 1)).encode(), "300000")
        self.assertEqual(len(parse_document("a.txt", b"a" * MAX_CONTENT_CHARACTERS)["content"]), MAX_CONTENT_CHARACTERS)

    def test_docx_paragraphs_headings_hyperlinks_and_whitespace(self):
        body = (paragraph("第一章 绪论", "Heading1") + paragraph("研究方法", "Heading2")
                + '<w:p><w:r><w:t>正文 </w:t></w:r><w:hyperlink><w:r><w:t>链接显示文本</w:t></w:r></w:hyperlink>'
                  '<w:r><w:tab/><w:t>列二</w:t><w:br/><w:t>换行后</w:t></w:r></w:p>')
        original = package(body)
        result = parse_document("论文本体.docx", original)
        self.assertEqual(result["source_type"], "docx")
        self.assertEqual(result["content"], "# 第一章 绪论\n\n## 研究方法\n\n正文 链接显示文本\t列二\n换行后")
        self.assertIn(COPY_WARNING, result["warnings"])
        self.assertTrue(original.startswith(b"PK"))

    def test_all_six_heading_levels(self):
        content = parse_document("a.docx", package("".join(paragraph("H" + str(i), "Heading" + str(i)) for i in range(1, 7))))["content"]
        for i in range(1, 7):
            self.assertIn("#" * i + " H" + str(i), content)

    def test_custom_heading_styles_and_inheritance(self):
        styles = ('<w:styles xmlns:w="' + W + '">'
                  '<w:style w:type="paragraph" w:styleId="TitleCustom"><w:name w:val="标题 3"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Derived"><w:basedOn w:val="TitleCustom"/></w:style>'
                  '</w:styles>')
        rel = '<Relationship Id="styles" Type="' + OFFICE + 'styles" Target="styles.xml"/>'
        result = parse_document("a.docx", package(paragraph("三级标题", "Derived"), {"word/styles.xml": styles}, rel))
        self.assertEqual(result["content"], "### 三级标题")

    def test_tables_keep_rows_and_cell_text(self):
        table = '<w:tbl><w:tr><w:tc>' + paragraph("组别") + '</w:tc><w:tc>' + paragraph("数量") + '</w:tc></w:tr>'
        table += '<w:tr><w:tc>' + paragraph("实验组") + '</w:tc><w:tc>' + paragraph("38") + '</w:tc></w:tr></w:tbl>'
        result = parse_document("a.docx", package(table))
        self.assertEqual(result["content"], "| 组别 | 数量 |\n| --- | --- |\n| 实验组 | 38 |")
        self.assertTrue(any("表格" in message for message in result["warnings"]))

    def test_omml_fraction_scripts_and_warning(self):
        math = ('<w:p><m:oMath><m:f><m:num><m:r><m:t>x</m:t></m:r></m:num>'
                '<m:den><m:r><m:t>y</m:t></m:r></m:den></m:f>'
                '<m:sSup><m:e><m:r><m:t>z</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>'
                '</m:oMath></w:p>')
        result = parse_document("a.docx", package(math))
        self.assertEqual(result["content"], "[公式：(x)/(y)z^(2)]")
        self.assertTrue(any("未转换为可编辑公式" in message for message in result["warnings"]))

    def test_superscript_and_page_break(self):
        body = '<w:p><w:r><w:t>x</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t><w:br w:type="page"/><w:t>后页</w:t></w:r></w:p>'
        result = parse_document("a.docx", package(body))
        self.assertEqual(result["content"], "x^(2\n\n后页)")

    def test_footnote_and_endnote(self):
        body = '<w:p><w:r><w:t>正文</w:t><w:footnoteReference w:id="1"/><w:endnoteReference w:id="2"/></w:r></w:p>'
        notes = {"word/footnotes.xml": '<w:footnotes xmlns:w="' + W + '"><w:footnote w:id="1">' + paragraph("脚注证据") + '</w:footnote></w:footnotes>',
                 "word/endnotes.xml": '<w:endnotes xmlns:w="' + W + '"><w:endnote w:id="2">' + paragraph("尾注证据") + '</w:endnote></w:endnotes>'}
        rel = ''.join('<Relationship Id="' + kind + '" Type="' + OFFICE + kind + '" Target="' + kind + '.xml"/>' for kind in ['footnotes', 'endnotes'])
        content = parse_document("a.docx", package(body, notes, rel))["content"]
        self.assertIn("正文[^fn1][^en2]", content)
        self.assertIn("[^fn1]: 脚注证据", content)
        self.assertIn("[^en2]: 尾注证据", content)

    def test_missing_footnote_is_explicit(self):
        body = '<w:p><w:r><w:t>正文</w:t><w:footnoteReference w:id="1"/></w:r></w:p>'
        result = parse_document("a.docx", package(body))
        self.assertIn("[注释内容缺失]", result["content"])
        self.assertTrue(any("找不到" in message for message in result["warnings"]))

    def test_tracked_changes_and_field_display(self):
        body = ('<w:p><w:del><w:r><w:delText>旧句</w:delText></w:r></w:del>'
                '<w:ins><w:r><w:t>新句</w:t></w:r></w:ins>'
                '<w:fldSimple w:instr="HYPERLINK malicious"><w:r><w:t>已有域结果</w:t></w:r></w:fldSimple></w:p>')
        result = parse_document("a.docx", package(body))
        self.assertEqual(result["content"], "新句已有域结果")
        self.assertTrue(any("当前视图" in message for message in result["warnings"]))
        self.assertTrue(any("未更新域" in message for message in result["warnings"]))

    def test_external_hyperlinks_never_load(self):
        rel = '<Relationship Id="link" Type="' + OFFICE + 'hyperlink" Target="https://invalid.example/never-fetch" TargetMode="External"/>'
        with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
            result = parse_document("a.docx", package(paragraph("仅显示正文"), relationships=rel))
        self.assertEqual(result["content"], "仅显示正文")
        self.assertTrue(any("未访问链接" in message for message in result["warnings"]))

    def test_empty_and_image_only_docx(self):
        self.fail_import("a.docx", package(""), "没有可提取")
        self.fail_import("a.docx", package('<w:p><w:r><w:drawing/></w:r></w:p>'), "OCR")

    def test_renamed_macros_and_active_content(self):
        self.fail_import("a.docx", package(main_type="application/vnd.ms-word.document.macroEnabled.main+xml"), "宏")
        self.fail_import("a.docx", package(extras={"word/vbaProject.bin": b"macro"}), "宏")
        self.fail_import("a.docx", package(extras={"word/activeX/activeX1.bin": b"control"}), "活动控件")
        self.fail_import("a.docx", package('<w:altChunk/>'), "外部正文")

    def test_nonzip_and_broken_docx(self):
        self.fail_import("a.docx", b"not a zip", "标准 DOCX")
        self.fail_import("a.docx", bytes([80, 75, 3, 4]) + b"broken", "损坏")
        self.fail_import("a.docx", b"MZ" + package(), "标准 DOCX")

    def test_illegal_zip_paths(self):
        for name in ["../outside.xml", "/absolute.xml", "C:drive.xml", "word/./same.xml"]:
            with self.subTest(name=name):
                self.fail_import("a.docx", package(extras={name: b"bad"}), "路径")
        # Windows ZipInfo normalizes backslashes when creating fixtures, so corrupt
        # both ZIP name records only after serializing the valid archive.
        raw = package(extras={"wordXbad.xml": b"bad"}).replace(b"wordXbad.xml", b"word\\bad.xml")
        self.fail_import("a.docx", raw, "路径")

    def test_markup_compatibility_fallback_is_not_duplicated(self):
        body = ('<w:p><mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                '<mc:Choice><w:r><w:t>当前文本</w:t></w:r></mc:Choice>'
                '<mc:Fallback><w:r><w:t>备用文本</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>')
        self.assertEqual(parse_document("a.docx", package(body))["content"], "当前文本")

    def test_duplicate_member(self):
        data = BytesIO(package())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(data, "a") as archive:
                archive.writestr("word/document.xml", "duplicate")
        self.fail_import("a.docx", data.getvalue(), "重复")

    def test_encrypted_flag(self):
        data = bytearray(package())
        for signature, offset in [(bytes([80, 75, 3, 4]), 6), (bytes([80, 75, 1, 2]), 8)]:
            index = data.find(signature)
            flags = struct.unpack_from("<H", data, index + offset)[0]
            struct.pack_into("<H", data, index + offset, flags | 1)
        self.fail_import("a.docx", bytes(data), "加密")

    def test_zip_bomb_ratio_and_expanded_size(self):
        self.fail_import("a.docx", package(extras={"word/bomb.xml": b"a" * (1024 * 1024 + 1)}), "压缩比")
        with patch("app.document_import.MAX_EXPANDED_BYTES", 1024):
            self.fail_import("a.docx", package(extras={"word/large.xml": b"a" * 1500}), "20 MiB")
        with patch("app.document_import.MAX_ZIP_ENTRIES", 2):
            self.fail_import("a.docx", package(), "数量")

    def test_dtd_entities_and_utf16_dtd(self):
        xml = '<!DOCTYPE doc [<!ENTITY x "expanded">]><w:document xmlns:w="' + W + '"><w:body>' + paragraph("&x;") + '</w:body></w:document>'
        self.fail_import("a.docx", package(document=xml), "DTD")
        xml16 = '<?xml version="1.0" encoding="UTF-16"?>' + xml
        self.fail_import("a.docx", package(document=xml16.encode("utf-16")), "DTD")

    def test_invalid_xml_and_missing_body(self):
        self.fail_import("a.docx", package(paragraph("&undefined;")), "XML")
        self.fail_import("a.docx", package(document="<document><body/></document>"), "Word 正文")
        self.fail_import("a.docx", package(document='<w:document xmlns:w="' + W + '"/>'), "缺少正文")

    def test_required_opc_structure(self):
        self.fail_import("a.docx", package(root_relationships='<Relationships xmlns="' + REL + '"/>'), "主文档关系")
        rel = '<Relationships xmlns="' + REL + '"><Relationship Id="main" Type="' + OFFICE + 'officeDocument" Target="https://invalid.example/main.xml" TargetMode="External"/></Relationships>'
        self.fail_import("a.docx", package(root_relationships=rel), "本地主文档")

    def test_xml_depth_and_docx_character_limit(self):
        self.fail_import("a.docx", package('<w:sdt>' * 140 + paragraph("text") + '</w:sdt>' * 140), "嵌套过深")
        with patch("app.document_import.MAX_CONTENT_CHARACTERS", 4):
            self.fail_import("a.docx", package(paragraph("12345")), "300000")


if __name__ == "__main__":
    unittest.main()
