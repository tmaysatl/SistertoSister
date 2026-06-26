"""PDF watermarking + logo helpers for audit-trail stamping."""
import io
import logging
import urllib.request
from typing import Optional

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

LOGO_URL = (
    'https://customer-assets.emergentagent.com/job_audit-prep-hub/'
    'artifacts/3dundaal_FullLogo_Transparent_NoBuffer_SistertoSisterPHCP.png'
)
_LOGO_BYTES: Optional[bytes] = None


def _load_logo() -> Optional[bytes]:
    global _LOGO_BYTES
    if _LOGO_BYTES is None:
        try:
            with urllib.request.urlopen(LOGO_URL, timeout=10) as r:
                _LOGO_BYTES = r.read()
        except Exception as e:
            logging.warning(f'Could not fetch logo: {e}')
            _LOGO_BYTES = b''
    return _LOGO_BYTES if _LOGO_BYTES else None


def _make_watermark(
    viewer_name: str, ts: str, page_w: float, page_h: float
) -> bytes:
    """Create a single-page transparent watermark PDF with logo + footer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.saveState()
    c.translate(page_w / 2, page_h / 2)
    c.rotate(35)
    try:
        c.setFillColorRGB(0.40, 0.12, 0.12, alpha=0.10)
    except TypeError:
        c.setFillColorRGB(0.40, 0.12, 0.12)
    c.setFont('Helvetica-Bold', 48)
    c.drawCentredString(0, 0, 'SISTER TO SISTER, PHCP')
    c.restoreState()
    logo = _load_logo()
    if logo:
        try:
            img = ImageReader(io.BytesIO(logo))
            c.drawImage(
                img, page_w - 80, page_h - 50, width=60, height=40,
                preserveAspectRatio=True, mask='auto',
            )
        except Exception as e:
            logging.warning(f'Logo draw error: {e}')
    c.setFillColorRGB(0.20, 0.20, 0.20)
    c.setFont('Helvetica', 7)
    c.drawString(
        20, 12,
        f'Sister to Sister, PHCP  \u00b7  Viewed by {viewer_name}  \u00b7  {ts}',
    )
    c.drawRightString(page_w - 20, 12, 'AUDIT TRAIL')
    c.save()
    return buf.getvalue()


def stamp_pdf(pdf_bytes: bytes, viewer_name: str, ts: str) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        wm_reader = PdfReader(io.BytesIO(_make_watermark(viewer_name, ts, w, h)))
        page.merge_page(wm_reader.pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
