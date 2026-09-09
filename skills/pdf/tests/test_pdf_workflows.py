"""Behavioral checks for OCR quality gates and non-destructive PDF operations.

Uses the existing pypdf, ReportLab and Pillow dependencies. Browser, OCR-model
and Office integration checks run separately against real installed engines.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from reportlab.pdfgen import canvas

import _pdf_common as common
import create_design_pdf
import edit_pdf
import ocr_text
import render_pdf


class PDFWorkflows(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pdf-regression-")
        self.root = Path(self.directory.name).resolve()
        self.root_patch = patch.object(common, "PDF_OUTPUT_ROOT", self.root)
        self.root_patch.start()
        self.source = self.root / "source.pdf"
        c = canvas.Canvas(str(self.source), pagesize=(600, 800))
        c.drawString(30, 750, "Original content 123")
        form = c.acroForm
        form.textfield(name="name", x=30, y=650, width=200, height=25, maxlen=40)
        form.checkbox(name="agree", x=30, y=600, checked=False)
        form.radio(name="mode", value="A", x=30, y=550, selected=True)
        form.radio(name="mode", value="B", x=80, y=550, selected=False)
        form.choice(name="country", value="CN", options=["CN", "US"], x=30, y=480, width=150, height=30)
        image = Image.new("RGB", (24, 16), "#8a3a2a")
        c.drawInlineImage(image, 350, 500, width=96, height=64)
        c.showPage()
        c.save()
        self.original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        self.root_patch.stop()
        self.directory.cleanup()

    def args(self, operation, **values):
        return SimpleNamespace(operation=operation, input=str(self.source),
                               output=str(self.root / "result.pdf"), overwrite=False,
                               data=None, data_file=None, pages=None, **values)

    def test_form_inventory_and_roundtrip_all_supported_types(self):
        fields = edit_pdf.execute(self.args("form-info", offset=0, limit=50))
        infos = {field["id"]: field for field in fields["fields"]}
        self.assertEqual(infos["agree"]["type"], "checkbox")
        self.assertEqual(set(infos["agree"]["states"]), {"/Off", "/Yes"})
        self.assertEqual(set(infos["mode"]["states"]), {"/A", "/B"})
        self.assertFalse(fields["has_xfa"])
        args = self.args("form-fill")
        args.data = json.dumps({"name": "Alice 123", "agree": True, "mode": "B", "country": "US"})
        result = edit_pdf.execute(args)
        updated = PdfReader(result["path"])
        fields = updated.get_fields()
        self.assertEqual(fields["name"]["/V"], "Alice 123")
        self.assertEqual(fields["agree"]["/V"], "/Yes")
        self.assertEqual(fields["mode"]["/V"], "/B")
        self.assertEqual(fields["country"]["/V"], "US")
        widgets = [ref.get_object() for ref in updated.pages[0]["/Annots"]]
        checkbox = next(widget for widget in widgets if widget.get("/T") == "agree")
        self.assertEqual(checkbox["/AS"], "/Yes")
        radio = [widget for widget in widgets if widget.get("/Parent")]
        self.assertEqual(sorted(str(widget["/AS"]) for widget in radio), ["/B", "/Off"])
        self.assertIn("Original content 123", updated.pages[0].extract_text())

    def test_checkbox_false_is_off(self):
        args = self.args("form-fill")
        args.data = '{"agree":false}'
        result = edit_pdf.execute(args)
        self.assertEqual(PdfReader(result["path"]).get_fields()["agree"]["/V"], "/Off")

    def test_invalid_form_input_does_not_publish(self):
        for data in [{"missing":"x"}, {"agree":"false"}, {"mode":True}, {"mode":"Off"},
                     {"country":"ZZ"}, {"country":["CN","US"]}, {"name":"x"*41}]:
            with self.subTest(data=data):
                args = self.args("form-fill")
                args.data = json.dumps(data)
                with self.assertRaises(ValueError):
                    edit_pdf.execute(args)
                self.assertFalse(Path(args.output).exists())

    def test_form_inventory_handles_indirect_appearance(self):
        writer = PdfWriter(clone_from=self.source)
        for ref in writer.pages[0]["/Annots"]:
            widget = ref.get_object()
            if "/AP" in widget:
                widget[NameObject("/AP")] = writer._add_object(widget["/AP"])
        alternative = self.root / "indirect.pdf"
        writer.write(alternative)
        args = self.args("form-info", offset=0, limit=50)
        args.input = str(alternative)
        result = edit_pdf.execute(args)
        self.assertEqual(result["field_count"], 4)

    def test_xfa_rejected_for_fill(self):
        writer = PdfWriter(clone_from=self.source)
        writer.root_object["/AcroForm"][NameObject("/XFA")] = TextStringObject("unsupported")
        alternative = self.root / "xfa.pdf"
        writer.write(alternative)
        args = self.args("form-fill")
        args.input = str(alternative)
        args.data = '{"name":"Alice"}'
        with self.assertRaisesRegex(ValueError, "XFA"):
            edit_pdf.execute(args)
        self.assertFalse(Path(args.output).exists())

    def test_crop_keeps_content_and_forms(self):
        result = edit_pdf.execute(self.args("crop", box="50,50,500,700"))
        reader = PdfReader(result["path"])
        self.assertEqual(list(reader.pages[0].cropbox), [50, 50, 500, 700])
        self.assertIn("Original content 123", reader.pages[0].extract_text())
        self.assertEqual(len(reader.get_fields()), 4)

    def test_crop_rejects_out_of_bounds_and_nonfinite_values(self):
        for box in ["-1,0,300,400", "0,0,601,800", "10,0,0,20", "0,0,nan,20"]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                edit_pdf.execute(self.args("crop", box=box))
        self.assertFalse((self.root / "result.pdf").exists())

    def test_metadata_preserves_forms_and_unspecified_fields(self):
        original = PdfReader(self.source).metadata
        args = self.args("metadata")
        args.data = '{"Title":"中文报告","Author":"Test"}'
        result = edit_pdf.execute(args)
        reader = PdfReader(result["path"])
        self.assertEqual(reader.metadata.title, "中文报告")
        self.assertEqual(reader.metadata.producer, original.producer)
        self.assertEqual(len(reader.get_fields()), 4)

    def test_embedded_image_has_original_dimensions(self):
        args = self.args("extract-images", output_dir=str(self.root / "images"), start_image=0, max_images=20)
        result = edit_pdf.execute(args)
        self.assertEqual(len(result["images"]), 1)
        with Image.open(result["images"][0]["path"]) as embedded:
            self.assertEqual(embedded.size, (24, 16))

    def test_existing_output_is_preserved_on_failure(self):
        target = self.root / "result.pdf"
        target.write_bytes(b"previous result")
        args = self.args("metadata")
        args.overwrite = True
        args.data = '{"Unsupported":"value"}'
        with self.assertRaises(ValueError):
            edit_pdf.execute(args)
        self.assertEqual(target.read_bytes(), b"previous result")

    def test_output_cannot_replace_source(self):
        args = self.args("crop", box="0,0,100,100")
        args.output = str(self.source)
        args.overwrite = True
        with self.assertRaises(ValueError):
            edit_pdf.execute(args)

    def test_output_directory_boundary(self):
        args = self.args("crop", box="0,0,100,100")
        args.output = str(self.root.parent / "outside.pdf")
        with self.assertRaises(ValueError):
            edit_pdf.execute(args)

    def test_missing_cjk_maps_rejects_incomplete_preview(self):
        destination = self.root / 'preview'
        args = render_pdf._parse_args(['--input', str(self.source), '--output-dir', str(destination)])
        completed = SimpleNamespace(returncode=0, stdout='', stderr="Syntax Error: Missing language pack for 'Adobe-GB1' mapping")
        with patch.object(render_pdf.shutil, 'which', return_value=sys.executable), patch.object(render_pdf.subprocess, 'run', return_value=completed):
            with self.assertRaisesRegex(RuntimeError, 'poppler-data'):
                render_pdf._render(args)
        self.assertEqual(list(destination.glob('*.png')), [])


class OCRQuality(unittest.TestCase):
    def recognize(self, texts, scores):
        boxes = [[[0, i*30], [100, i*30], [100, i*30+20], [0, i*30+20]] for i in range(len(texts))]
        engine = lambda _: SimpleNamespace(txts=texts, scores=scores, boxes=boxes)
        return ocr_text._ocr_page(engine, Path("fixture.png"))

    def test_mixed_confidence_does_not_certify_uncertain_amount(self):
        result = self.recognize(["Reliable document heading and text", "9999.99"], [.99, .3])
        self.assertEqual(result["status"], "good")
        self.assertNotIn("9999.99", result["text"])
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["review_regions"][0]["text_candidate"], "9999.99")
        self.assertIsNotNone(result["review_regions"][0]["box"])

    def test_unusable_page_is_not_returned_as_reliable_text(self):
        for texts, scores in [(["uncertain document"], [.3]), (["Hi"], [.99]), ([], [])]:
            with self.subTest(texts=texts):
                result = self.recognize(texts, scores)
                self.assertEqual(result["text"], "")
                self.assertFalse(result["usable_for_summary"])
                self.assertTrue(result["needs_review"])

    def test_good_ocr_needs_no_model_assistance(self):
        result = self.recognize(["中文识别测试 12345", "English document"], [.99, .98])
        self.assertFalse(result["needs_review"])
        self.assertIn("中文识别测试", result["text"])

    def test_invalid_confidence_is_not_treated_as_certain(self):
        result = self.recognize(['Unknown confidence amount 9999'], [float('nan')])
        self.assertEqual(result['text'], '')
        self.assertTrue(result['needs_review'])

    def test_missing_local_models_fails_before_engine_initialization(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = SimpleNamespace(__file__=str(Path(folder) / "__init__.py"))
            fake.RapidOCR = lambda **_: self.fail("Engine must not download missing models")
            with patch.dict(sys.modules, {"rapidocr":fake}):
                with self.assertRaisesRegex(RuntimeError, "本地 OCR 模型"):
                    ocr_text._create_ocr_engine()


class StaticDocument(unittest.TestCase):
    def test_active_content_is_rejected(self):
        for html in ["<script>alert(1)</script>", '<svg onload="x()"></svg>',
                     '<iframe src="https://example.com"></iframe>',
                     '<a href="java&#x73;cript:alert(1)">x</a>',
                     '<meta http-equiv="refresh" content="0;url=https://example.com">',
                     '<img src="file:///etc/passwd">']:
            with self.subTest(html=html), self.assertRaises(ValueError):
                create_design_pdf.StaticHTML().feed(html)

    def test_static_math_diagram_and_svg_are_accepted(self):
        html = '<h1>报告</h1><p class="math-inline">E=mc^2</p><div class="mermaid">flowchart LR\nA-->B</div><svg><rect width="10" height="10" /></svg>'
        create_design_pdf.StaticHTML().feed(html)


if __name__ == "__main__":
    unittest.main()
