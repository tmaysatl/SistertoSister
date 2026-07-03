"""PDF field extraction utilities.

Phase 1 backend feature. Given a PDF file on disk, return a normalised
field schema so downstream consumers (native fillable form UI, e-sign
workflow, audit binder overlay, etc.) can render the form without
re-parsing the PDF each time.

Public entry point: `parse_pdf(pdf_path) -> list[dict]`.
Prefers AcroForm widget introspection (accurate field names + rects);
falls back to regex over rendered text when the PDF is "flat" (no widget
annotations — a scan or a hand-drawn template).

Schema shape (one dict per field):
    {
      "field_name": str,
      "field_type": str,        # "text" | "checkbox" | "radio" |
                                #  "signature" | "combobox" | "listbox" |
                                #  "button"
      "page":       int,        # 1-indexed
      "position":   {"x0": float, "y0": float,
                     "x1": float, "y1": float},
      "options":    list[str],  # populated only for choice fields; else []
      # informational extras (never required by the contract but useful
      # to consumers; safe to ignore):
      "required":   bool,       # widget.field_flags & 2  (Required)
      "value":      Optional[str],   # current default value
      "source":     "acroform" | "text-heuristic",
    }
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import pymupdf  # a.k.a. fitz — installed via `pip install pymupdf`

logger = logging.getLogger(__name__)

# --- PyMuPDF widget type integer -> normalised string ----------------------
# See PDF_WIDGET_TYPE_* constants in pymupdf. Uses `field_type_string` when
# available; falls back to the integer table below for older builds.
_WIDGET_TYPE_MAP = {
    1: "button",
    2: "checkbox",
    3: "radio",
    4: "text",
    5: "listbox",
    6: "combobox",
    7: "signature",
}
_WIDGET_STRING_MAP = {
    "Text":         "text",
    "CheckBox":     "checkbox",
    "RadioButton":  "radio",
    "ComboBox":     "combobox",
    "ListBox":      "listbox",
    "Signature":    "signature",
    "Button":       "button",
    "PushButton":   "button",
}


def _widget_type(widget) -> str:
    s = getattr(widget, "field_type_string", None)
    if s and s in _WIDGET_STRING_MAP:
        return _WIDGET_STRING_MAP[s]
    ft = getattr(widget, "field_type", None)
    if isinstance(ft, int):
        return _WIDGET_TYPE_MAP.get(ft, "text")
    return "text"


def _rect_dict(rect) -> Dict[str, float]:
    try:
        return {
            "x0": round(float(rect.x0), 2),
            "y0": round(float(rect.y0), 2),
            "x1": round(float(rect.x1), 2),
            "y1": round(float(rect.y1), 2),
        }
    except Exception:
        return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}


# ---------------------------------------------------------------------------
# 1) AcroForm path — accurate, structured
# ---------------------------------------------------------------------------
def extract_acroform_fields(pdf_path: str | Path) -> List[Dict[str, Any]]:
    """Read AcroForm widget annotations from `pdf_path`.

    Returns an empty list if the PDF has no AcroForm widgets (i.e. it's a
    flat scan or template) — the caller should then fall through to the
    text heuristic.
    """
    p = str(pdf_path)
    out: List[Dict[str, Any]] = []
    with pymupdf.open(p) as doc:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            try:
                widgets = list(page.widgets() or [])
            except Exception as e:
                logger.warning("pdf_parser: widgets() failed on page %d of %s: %s",
                               page_index + 1, p, e)
                widgets = []
            for w in widgets:
                name = (getattr(w, "field_name", None) or "").strip()
                if not name:
                    continue
                ftype = _widget_type(w)
                options: List[str] = []
                # Choice fields expose `choice_values` (list of strings or
                # list of [export, display] pairs).
                cvs = getattr(w, "choice_values", None)
                if cvs:
                    for item in cvs:
                        if isinstance(item, (list, tuple)) and item:
                            options.append(str(item[-1]))
                        else:
                            options.append(str(item))
                # Required flag: PDF spec, bit 2 of field_flags.
                flags = int(getattr(w, "field_flags", 0) or 0)
                required = bool(flags & 2)
                value = getattr(w, "field_value", None)
                if value is not None and not isinstance(value, str):
                    try:
                        value = str(value)
                    except Exception:
                        value = None
                out.append({
                    "field_name": name,
                    "field_type": ftype,
                    "page": page_index + 1,
                    "position": _rect_dict(w.rect),
                    "options": options,
                    "required": required,
                    "value": value or None,
                    "source": "acroform",
                })
    return out


# ---------------------------------------------------------------------------
# 2) Text-based heuristic fallback — for flat PDFs with no widgets
# ---------------------------------------------------------------------------
# Match "Label: ______" or "Label ______" (2+ underscores).
# Kept intentionally permissive so it also catches "First Name: ___" and
# "D.O.B ___/___/___". Rejects labels shorter than 2 chars or purely
# numeric to reduce noise.
_LABEL_UNDERSCORE_RE = re.compile(
    r"""
    (?P<label>[A-Za-z][\w./&()\-\ ]{1,80}?)   # label (letters/spaces, up to 80c)
    \s*:?\s*                                   # optional colon
    (?P<slots>(?:_+\s*[/\-]?\s*){2,}|_{3,})    # 3+ underscores OR grouped ___/___
    """,
    re.VERBOSE,
)

# Checkbox / radio markers seen in scanned templates. Includes the classic
# empty-box glyphs, "[ ]", and the "hh" ligature that PDF text extraction
# emits when the source used a Wingdings/Zapf square (as we saw in the
# skilled-nurse sample: "hh YES hh NO").
_CHECKBOX_MARKERS = ("☐", "□", "\uf0a8", "\uf06f", "hh ", "[ ]", "[]")

# Try to spot inline choice groups like "YES / NO" or "M / F" following
# checkbox markers so we can populate `options` for the heuristic checkbox
# field.
_YES_NO_RE = re.compile(r"\b(YES|NO)\b", re.IGNORECASE)


def _clean_label(raw: str) -> str:
    s = raw.strip().rstrip(":").rstrip()
    # Collapse whitespace and strip common lead/trail junk.
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" \t.-_")
    return s


def extract_fields_from_text(pdf_path: str | Path) -> List[Dict[str, Any]]:
    """Fallback extractor: regex the rendered text of each page.

    Detects two patterns per page line:
      1. `Label: ______`  → text field (underscore-run acts as the input)
      2. `Label` followed by a checkbox marker (☐, "[ ]", "hh ") →
         checkbox field. If YES/NO appear on the same line, they become
         `options`.

    Position is approximated by locating the label span on the page via
    `page.search_for(label)`. If that lookup fails the position falls back
    to zeros — consumers should treat position as a hint, not ground truth.
    """
    p = str(pdf_path)
    out: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    with pymupdf.open(p) as doc:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            text = page.get_text("text") or ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # ---- Pattern A: underscore text fields ----
                for m in _LABEL_UNDERSCORE_RE.finditer(line):
                    label = _clean_label(m.group("label"))
                    if len(label) < 2 or label.isdigit():
                        continue
                    unique = _dedupe(label, seen_names)
                    out.append({
                        "field_name": unique,
                        "field_type": "text",
                        "page": page_index + 1,
                        "position": _locate_label(page, label),
                        "options": [],
                        "required": False,
                        "value": None,
                        "source": "text-heuristic",
                    })

                # ---- Pattern B: checkbox markers ----
                if any(mk in line for mk in _CHECKBOX_MARKERS):
                    # Strip markers to derive a stable label for the group.
                    label = line
                    for mk in _CHECKBOX_MARKERS:
                        label = label.replace(mk, " ")
                    label = _clean_label(label)
                    if not label:
                        continue
                    # Split trailing YES/NO tokens off the label so the
                    # label is the human question, not "Question YES NO".
                    label = _YES_NO_RE.sub("", label).strip(" ?")
                    if len(label) < 2:
                        continue
                    yn = [g.upper() for g in _YES_NO_RE.findall(raw_line)]
                    options = sorted(set(yn)) if yn else []
                    unique = _dedupe(label, seen_names)
                    out.append({
                        "field_name": unique,
                        "field_type": "checkbox",
                        "page": page_index + 1,
                        "position": _locate_label(page, label),
                        "options": options,
                        "required": False,
                        "value": None,
                        "source": "text-heuristic",
                    })
    return out


def _dedupe(name: str, seen: set[str]) -> str:
    """Return `name`, or `name (2)`, `name (3)` ... to avoid collisions."""
    if name not in seen:
        seen.add(name)
        return name
    i = 2
    while f"{name} ({i})" in seen:
        i += 1
    unique = f"{name} ({i})"
    seen.add(unique)
    return unique


def _locate_label(page, label: str) -> Dict[str, float]:
    # `search_for` returns a list of Rects. Take the first hit as a best
    # effort. If nothing matches, drop to zeros (position is a hint).
    try:
        rects = page.search_for(label[:60], quads=False) or []
        if rects:
            return _rect_dict(rects[0])
    except Exception:
        pass
    return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}


# ---------------------------------------------------------------------------
# 3) Top-level entry point
# ---------------------------------------------------------------------------
def parse_pdf(pdf_path: str | Path) -> List[Dict[str, Any]]:
    """Extract a field schema from `pdf_path`.

    Tries the AcroForm path first (accurate) and, if that returns nothing
    usable, falls back to the text-based heuristic. Never raises for
    normal parse failures — returns an empty list and logs a warning so
    the caller's upload path stays hot.
    """
    try:
        fields = extract_acroform_fields(pdf_path)
    except Exception as e:
        logger.warning("pdf_parser: acroform extraction failed for %s: %s",
                       pdf_path, e)
        fields = []

    if fields:
        return fields

    try:
        return extract_fields_from_text(pdf_path)
    except Exception as e:
        logger.warning("pdf_parser: text extraction failed for %s: %s",
                       pdf_path, e)
        return []


__all__ = [
    "extract_acroform_fields",
    "extract_fields_from_text",
    "parse_pdf",
]
