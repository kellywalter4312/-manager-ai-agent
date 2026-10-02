"""Structured perception (GM-03): what is *where* in a picture or a moving
one, as data - boxes, counts, and identity across frames - rather than a
paragraph about it.

    contract.py   Detection, FramePerception, PerceptionProvider
    motion.py     "motion": moving regions against a background model
                  (deterministic, numpy) - any subject that moves
    regions.py    "regions": regions distinct from their background in a
                  still (deterministic, numpy) - any subject that stands out
    labelled.py   "local_vision": either of the above, each region then
                  named by the local vision model when one is installed
    factory.py    the provider axis: selected by name, replaceable

Nothing here names a subject. A detection says a region is there; only a
backend that can honestly name what it is ever fills ``label``.
"""
