"""Locked, hashed e-signature PDF generation.

New module (additive — no existing file's behavior changes by importing
this). Gives every document-signing code path in server.py a common way to
turn a submission into a permanent, tamper-evident record:

  1. sha256_hex()              -- integrity hash for any finished PDF.
  2. fill_acroform_pdf()       -- fills a REAL AcroForm PDF (one of the
                                   fillable forms forms.py already
                                   generates) with submitted values, using
                                   the field list pdf_parser.py already
                                   extracted, then flattens + strips the
                                   form's interactivity so the result is a
                                   locked, non-editable PDF.
  3. render_generic_submission_pdf() -- fallback "cover sheet" renderer
                                   (label/value list + signature) for
                                   documents that have no real AcroForm
                                   fields to fill (e.g. a flat, scanned
                                   upload). Modeled on
                                   form_schemas.render_filled_pdf but driven
                                   by an arbitrary field list instead of a
                                   hand-authored schema.

"Locked" here means: once generated, the PDF's form fields (if any) are
flattened into the page content and the interactive widgets + /AcroForm
are removed, so the document can no longer be edited in a PDF viewer.
sha256_hex() gives callers a fingerprint to store alongside the record so
any future substitution of the bytes is detectable -- callers are
responsible for persisting/comparing the hash; this module only computes
it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
from typing import Any, Dict, List, Optional

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_form_interactivity(writer: PdfWriter) -> None:
    """Remove interactive widget annotations + /AcroForm from `writer` in
    place. Call this AFTER any values are already baked into the page
    content (flatten=True fill, or a merged overlay like a stamped
    signature image) -- the visible content survives; only the ability to
    re-edit it in a PDF viewer is removed. A no-op (safe to call
    unconditionally) on a document that had no form fields to begin with.
    """
    try:
        for page in writer.pages:
            if "/Annots" in page:
                del page[NameObject("/Annots")]
        if "/AcroForm" in writer.root_object:
            del writer.root_object[NameObject("/AcroForm")]
    except Exception as e:
        logger.warning("strip_form_interactivity: failed (content is still filled): %s", e)


# ---------------------------------------------------------------------------
# Tier 2 — fill an actual AcroForm PDF (covers every fillable form
# forms.py generates, not just the 5 curated in form_schemas.py)
# ---------------------------------------------------------------------------
def fill_acroform_pdf(
    pdf_bytes: bytes, values: Dict[str, Any], fields: List[Dict[str, Any]],
) -> Optional[bytes]:
    """Fill `pdf_bytes`'s AcroForm fields with `values` (keyed by
    field_name, same shape the frontend submits) and return a locked,
    flattened PDF. `fields` is the cached extraction result from
    pdf_parser.extract_acroform_fields (field_name/field_type/page/
    position/...) -- callers already have this cached in
    db.field_schemas, so this function never re-parses the PDF itself.

    Returns None (does nothing) if `fields` contains no acroform-sourced
    entries -- callers should fall back to render_generic_submission_pdf
    in that case. Never raises: a field-level failure is logged and
    skipped so one bad value can't block the whole submission.
    """
    acro_fields = [f for f in (fields or []) if f.get("source") == "acroform"]
    if not acro_fields:
        return None
    by_name = {f["field_name"]: f for f in acro_fields}

    text_values: Dict[str, Any] = {}
    image_overlays: List[tuple] = []  # (page_index, position, image_bytes)

    for name, val in (values or {}).items():
        f = by_name.get(name)
        if f is None or val is None or val == "":
            continue
        ftype = f.get("field_type", "text")

        # A signature captured as a drawn image arrives as a data: URL
        # regardless of the underlying widget's declared type (today's
        # generated PDFs expose the signature line as a plain text field
        # named sig_*, so this guards a future/uploaded PDF that maps it
        # to field_type "signature" too) -- draw it as an image overlay
        # rather than dumping raw base64 into a text field.
        if isinstance(val, str) and val.startswith("data:image"):
            try:
                raw_b64 = val.split(",", 1)[1]
                img_bytes = base64.b64decode(raw_b64)
                image_overlays.append(
                    (max(int(f.get("page") or 1) - 1, 0), f.get("position") or {}, img_bytes)
                )
            except Exception as e:
                logger.warning("fill_acroform_pdf: signature decode failed for %s: %s", name, e)
            continue

        if ftype == "checkbox":
            if val:  # truthy -> checked; falsy -> leave unset (defaults to Off)
                text_values[name] = "/Yes"
        elif ftype == "radio":
            if isinstance(val, str) and val:
                text_values[name] = val if val.startswith("/") else f"/{val}"
        elif ftype in ("combobox", "listbox"):
            if isinstance(val, list):
                text_values[name] = [str(v) for v in val]
            else:
                text_values[name] = str(val)
        else:  # text (and any other/unknown type -- safest as plain text)
            if isinstance(val, list):
                text_values[name] = ", ".join(str(v) for v in val)
            elif isinstance(val, bool):
                text_values[name] = "Yes" if val else "No"
            else:
                text_values[name] = str(val)

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        writer.append(reader)
    except Exception as e:
        logger.warning("fill_acroform_pdf: could not open source PDF: %s", e)
        return None

    if text_values:
        for page in writer.pages:
            try:
                writer.update_page_form_field_values(
                    page, text_values, auto_regenerate=False, flatten=True,
                )
            except Exception as e:
                logger.warning("fill_acroform_pdf: field update failed: %s", e)

    for page_index, pos, img_bytes in image_overlays:
        try:
            if page_index >= len(writer.pages):
                continue
            page = writer.pages[page_index]
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            x0 = float(pos.get("x0") or 40)
            y0 = float(pos.get("y0") or 40)
            x1 = float(pos.get("x1") or (x0 + 160))
            y1 = float(pos.get("y1") or (y0 + 40))
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            img = ImageReader(io.BytesIO(img_bytes))
            c.drawImage(
                img, x0, y0, width=max(x1 - x0, 20), height=max(y1 - y0, 14),
                preserveAspectRatio=True, mask="auto",
            )
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            page.merge_page(overlay)
        except Exception as e:
            logger.warning("fill_acroform_pdf: signature overlay failed: %s", e)

    # Lock: every value is now baked into each page's content stream
    # (flatten=True above, plus the merged overlays) -- remove the
    # interactive widgets + /AcroForm so the result can't be re-edited.
    strip_form_interactivity(writer)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Tier 3 — generic fallback "cover sheet" for documents with no real
# AcroForm fields (e.g. a flat scanned upload with only text-heuristic
# matches). Mirrors form_schemas.render_filled_pdf's layout but is driven
# by an arbitrary field list instead of a hand-authored schema.
# ---------------------------------------------------------------------------
_DISAMBIG_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


def _clean_field_label(name: str) -> str:
    s = _DISAMBIG_SUFFIX_RE.sub("", name or "")
    return s.strip(" \t\"':").strip()


def render_generic_submission_pdf(
    title: str,
    field_defs: List[Dict[str, Any]],
    values: Dict[str, Any],
    signature_b64: Optional[str],
    submitter_name: str,
) -> bytes:
    """Generic completed-form summary: branded header + every known field
    as "Label: Value" (in the order `field_defs` provides, normally the
    extracted schema order) + the signature image at the bottom.

    `field_defs` items need only `field_name` (and optionally
    `field_type` to skip signature-typed fields, rendered separately).
    Multi-page safe -- pages forward instead of truncating.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    page_w, page_h = letter

    def new_page(continued: bool = False) -> float:
        c.setFillColor(colors.white)
        c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#204231"))
        c.rect(0, page_h - 60, page_w, 60, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(0.6 * inch_pt, page_h - 30, "Sister to Sister, PHCP")
        c.setFont("Helvetica", 11)
        subtitle = f"{title} (continued)" if continued else title
        c.drawString(0.6 * inch_pt, page_h - 48, subtitle)
        return page_h - 80

    inch_pt = 72.0
    y = new_page()
    for f in field_defs or []:
        if f.get("field_type") == "signature":
            continue
        name = f.get("field_name") or ""
        val = values.get(name, "")
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        if isinstance(val, bool):
            val = "Yes" if val else "No"
        if isinstance(val, str) and val.startswith("data:image"):
            continue  # embedded separately as the signature below
        text = str(val) if val not in (None, "") else "—"

        if y < 1.3 * inch_pt:
            c.showPage()
            y = new_page(continued=True)

        c.setFillColor(colors.HexColor("#0E1A12"))
        c.setFont("Helvetica-Bold", 9)
        c.drawString(0.6 * inch_pt, y, _clean_field_label(name) + ":")
        c.setFont("Helvetica", 10)
        for line in simpleSplit(text, "Helvetica", 10, page_w - 1.3 * inch_pt):
            if y < 1.3 * inch_pt:
                c.showPage()
                y = new_page(continued=True)
            c.drawString(2.4 * inch_pt, y, line)
            y -= 14
        y -= 8

    if signature_b64:
        try:
            raw = signature_b64.split(",", 1)[-1]
            sig_bytes = base64.b64decode(raw)
            if y < 1.8 * inch_pt:
                c.showPage()
                y = new_page(continued=True)
            img = ImageReader(io.BytesIO(sig_bytes))
            c.setFillColor(colors.HexColor("#0E1A12"))
            c.setFont("Helvetica-Bold", 9)
            c.drawString(0.6 * inch_pt, y - 4, "Signature:")
            c.drawImage(
                img, 0.6 * inch_pt, y - 70, width=3.0 * inch_pt, height=60,
                preserveAspectRatio=True, mask="auto",
            )
            y -= 80
        except Exception as e:
            logger.warning("render_generic_submission_pdf: signature draw failed: %s", e)

    c.setFillColor(colors.HexColor("#56615C"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        0.6 * inch_pt, 0.4 * inch_pt,
        f"Submitted by {submitter_name} via Sister to Sister, PHCP compliance app.",
    )
    c.save()
    return buf.getvalue()
