"""Regression checks for the merged document workflows; no Docker or network required."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml.etree import _Element
from reportlab.pdfgen.canvas import Canvas

import _docx_common as common
import compile_typst

sys.dont_write_bytecode = True


class DocumentWorkflows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="docx-merge-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(patch.stopall)
        patch.object(common, "WORD_OUTPUT_ROOT", self.root).start()
        patch.object(compile_typst, "WORD_OUTPUT_ROOT", self.root).start()
        self.source = self.root / "source.docx"

    def call(self, script, *args):
        with patch.object(sys, "argv", [script, *map(str, args)]):
            return importlib.import_module(script).main()

    def fixture(self):
        doc = Document()
        p = doc.add_paragraph()
        p.add_run("BEFORE ")
        p.add_run("HEL").bold = True
        p.add_run("LO")
        p.add_run(" AFTER").italic = True
        doc.save(str(self.source))
        return doc

    def edit(self, operations, *args):
        output = self.root / "edited.docx"
        result = self.call("edit_document", "--input", self.source, "--output", output,
                           "--spec", json.dumps({"operations": operations}), *args)
        return result, Document(str(output))

    def test_cross_run_preserves_unmodified_styles_and_escapes_text(self):
        self.fixture()
        result, doc = self.edit([{"type": "replace_text", "find": "HELLO", "replace": "A & <B>"}])
        p = doc.paragraphs[0]
        self.assertEqual(p.text, "BEFORE A & <B> AFTER")
        self.assertTrue(next(r for r in p.runs if "A &" in r.text).bold)
        self.assertTrue(next(r for r in p.runs if " AFTER" in r.text).italic)
        validation = self.call("validate_document", "--input", result["path"])
        self.assertEqual(validation["status"], "valid")

    def test_single_and_split_matches_are_both_replaced(self):
        doc = self.fixture()
        doc.add_paragraph("HELLO HELLO")
        doc.save(str(self.source))
        result, doc = self.edit([{"type": "replace_text", "find": "HELLO", "replace": "NEW"}])
        self.assertEqual(result["operation_results"][0]["replacement_count"], 3)
        self.assertNotIn("HELLO", "".join(p.text for p in doc.paragraphs))

    def test_tracked_replacement_has_correct_nesting_and_accepted_text(self):
        self.fixture()
        result, doc = self.edit([{"type": "replace_text", "find": "HELLO", "replace": "A & <B>"}],
                                "--track-changes", "--author", "审阅 & A")
        self.assertEqual(result["tracked_replacement_count"], 1)
        self.assertFalse(doc.element.xpath(".//w:r/w:ins | .//w:r/w:del"))
        self.assertEqual(len(doc.element.xpath(".//w:p/w:ins")), 1)
        self.assertEqual(len(doc.element.xpath(".//w:p/w:del")), 1)
        for node in doc.element.xpath(".//w:ins | .//w:del"):
            self.assertEqual(node.get(qn("w:author")), "审阅 & A")
        accepted = deepcopy(doc.element)
        for node in accepted.xpath(".//w:del"):
            node.getparent().remove(node)
        for node in accepted.xpath(".//w:ins"):
            parent = node.getparent()
            index = parent.index(node)
            for child in list(node):
                parent.insert(index, child)
                index += 1
            parent.remove(node)
        self.assertEqual("".join(n.text or "" for n in accepted.xpath(".//w:t")), "BEFORE A & <B> AFTER")
        self.assertTrue(next(r for r in doc.paragraphs[0].runs if " AFTER" in r.text).italic)
        self.assertEqual(self.call("validate_document", "--input", result["path"])["status"], "valid")

    def test_multiple_tracked_matches_in_one_run(self):
        doc = Document()
        doc.add_paragraph("old old old")
        doc.save(str(self.source))
        result, doc = self.edit([{"type": "replace_text", "find": "old", "replace": "new"}], "--track-changes")
        self.assertEqual(result["tracked_replacement_count"], 3)
        self.assertEqual(len(doc.element.xpath(".//w:p/w:ins")), 3)
        ids = [node.get(qn("w:id")) for node in doc.element.xpath(".//w:ins | .//w:del")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_tracked_edits_reject_structural_operations(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError, "仅支持 replace_text"):
            self.edit([{"type": "append_blocks", "blocks": [{"type": "paragraph", "text": "x"}]}], "--track-changes")
        self.assertFalse((self.root / "edited.docx").exists())

    def test_tracked_edits_reject_bookmark_crossing(self):
        doc = self.fixture()
        mark = OxmlElement("w:bookmarkStart")
        mark.set(qn("w:id"), "7")
        mark.set(qn("w:name"), "target")
        cast(_Element, doc.paragraphs[0]._p).insert(2, mark)
        doc.save(str(self.source))
        with self.assertRaisesRegex(ValueError, "书签"):
            self.edit([{"type": "replace_text", "find": "HELLO", "replace": "new"}], "--track-changes")
        self.assertFalse((self.root / "edited.docx").exists())

    def test_patent_preset_has_independent_headers_and_page_number_restarts(self):
        output = self.root / "patent.docx"
        spec = {"claims": [{"number": 1, "text": "一种方法 & 装置", "dependent": False}],
                "specification": {"field": "领域", "detailed": ["实现 <描述>"]}, "abstract": "摘要"}
        result = self.call("create_document", "--preset", "patent", "--output", output, "--spec", json.dumps(spec))
        doc = Document(str(output))
        self.assertEqual(len(doc.sections), 3)
        self.assertEqual([s.header.paragraphs[0].text for s in doc.sections], ["权利要求书", "说明书", "摘要"])
        for section in doc.sections:
            page_numbers = section._sectPr.find(qn("w:pgNumType"))
            assert page_numbers is not None
            self.assertEqual(page_numbers.get(qn("w:start")), "1")
            self.assertFalse(section.header.is_linked_to_previous)
            self.assertFalse(section.footer.is_linked_to_previous)
        self.assertTrue(any(p.style is not None and p.style.name == "Heading 2" for p in doc.paragraphs))
        self.assertEqual(self.call("validate_document", "--input", result["path"])["status"], "valid")

    def test_invalid_patent_numbering_does_not_publish_a_file(self):
        output = self.root / "patent.docx"
        with self.assertRaisesRegex(ValueError, "连续编号"):
            self.call("create_document", "--preset", "patent", "--output", output,
                      "--spec", json.dumps({"claims": [{"number": 2, "text": "wrong"}]}))
        self.assertFalse(output.exists())

    def test_generic_document_api_still_supports_tables_and_sections(self):
        output = self.root / "generic.docx"
        spec = {"header": {"text": "Default"}, "blocks": [
            {"type": "heading", "text": "Report"},
            {"type": "table", "rows": [["Key", "Value"], ["A", "B"]]},
            {"type": "section_break"}, {"type": "paragraph", "text": "Second"}],
            "sections": [{"index": 1, "header": {"text": "Second header"}}]}
        self.call("create_document", "--output", output, "--spec", json.dumps(spec))
        doc = Document(str(output))
        self.assertEqual(doc.tables[0].cell(1, 1).text, "B")
        self.assertEqual([s.header.paragraphs[0].text for s in doc.sections], ["Default", "Second header"])

    def test_output_root_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "必须输出到"):
            common.output_file(str(self.root.parent / "outside.docx"))

    def test_html_extraction_decodes_entities_and_omits_scripts(self):
        path = self.root / "input.html"
        path.write_text('<p>A &amp; B</p><script>secret()</script><style>body{}</style><p>正文</p>')
        result = self.call("extract_source", "--input", path)
        self.assertEqual(result["text"], "A & B\n正文")
        self.assertTrue(result["usable_for_summary"])

    def test_text_cursor_preserves_every_character(self):
        path = self.root / "long.txt"
        text = "原始文本ABC & <> " * 90
        path.write_text(text)
        offset = 0
        pieces = []
        while True:
            result = self.call("extract_source", "--input", path, "--max-chars", 256, "--start-offset", offset)
            pieces.append(result["text"])
            if not result["has_more"]:
                break
            self.assertGreater(result["next_offset"], offset)
            offset = result["next_offset"]
        self.assertEqual("".join(pieces), text)

    def test_pdf_two_column_order_and_page_cursor(self):
        path = self.root / "columns.pdf"
        canvas = Canvas(str(path), pagesize=(600, 800))
        for y, left, right in [(730, "LEFT FIRST", "RIGHT FIRST"), (710, "LEFT SECOND", "RIGHT SECOND")]:
            canvas.drawString(50, y, left)
            canvas.drawString(350, y, right)
        canvas.showPage()
        canvas.drawString(50, 730, "NEXT PAGE")
        canvas.save()
        result = self.call("extract_source", "--input", path)
        self.assertEqual(result["columns"], 2)
        self.assertLess(result["text"].index("LEFT SECOND"), result["text"].index("RIGHT FIRST"))
        self.assertEqual(result["next_page"], 2)
        result = self.call("extract_source", "--input", path, "--page", 2)
        self.assertIn("NEXT PAGE", result["text"])
        self.assertFalse(result["has_more"])

    def test_empty_pdf_is_not_reported_as_reliable_text(self):
        path = self.root / "scan.pdf"
        canvas = Canvas(str(path))
        canvas.rect(50, 50, 50, 50, fill=1)
        canvas.showPage()
        canvas.save()
        result = self.call("extract_source", "--input", path)
        self.assertFalse(result["usable_for_summary"])
        self.assertTrue(result["needs_ocr"])

    def test_typst_template_keeps_user_content_as_json(self):
        text = '#read("/etc/passwd") & [not markup]'
        spec = {"name": "测试", "sections": [{"title": "经历", "items": [text]}]}
        source = compile_typst.resume_source(spec, self.root)
        self.assertNotIn(text, source.read_text())
        self.assertEqual(json.loads((self.root / "resume.json").read_text())["sections"][0]["items"][0], text)

    @unittest.skipUnless(shutil.which("typst"), "Typst CLI is not installed in this local test runtime")
    def test_typst_template_compiles_to_pdf(self):
        spec = {"name": "示例", "contact": ["example@example.com"],
                "sections": [{"title": "经历", "items": ["测试内容 & <text>"]}]}
        output = self.root / "resume.pdf"
        result = self.call("compile_typst", "--template", "resume", "--output", output, "--spec", json.dumps(spec))
        self.assertGreater(result["page_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
