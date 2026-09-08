"""Alfred's multimodal workspace foundation: a real, persistent, versioned
record of anything Alfred's work produces that is a genuine artifact
rather than an ordinary chat reply - a document today, an image, a
video, a diagram, or any future type tomorrow, all through the identical
identity/version/lifecycle contract in ``store.py``.

Agent-independent and type-independent by construction: nothing in this
package knows Alfred's name, and nothing in ``store.py`` knows what a
"document" actually contains (see ``models.py`` for the one concrete
content schema this checkpoint's vertical slice uses). A future agent or
artifact type reuses this exact package rather than duplicating it.
"""
