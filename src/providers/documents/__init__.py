"""Rendering real artifact content into real, exportable file formats -
PDF today (see ``pdf.py``), a future format tomorrow through the
identical ``DocumentRenderer`` contract. Genuinely separate from
``src/services/artifacts``: that package manages identity and
persistence and does not know how to draw a page; this package only
ever turns already-real content into already-real bytes.
"""
