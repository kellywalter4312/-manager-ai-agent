"""Observation history (GM-01) and the analysis over it (GM-09).

An *observation* is one value of one attribute of one entity at one moment,
from one source. It is how the ecosystem remembers what the world *was*, as
opposed to what a person told Alfred (Memory) or what Alfred made
(Artifacts). Nothing here knows any subject: an entity is whatever a
connection, a perception run, a stream, a capture or a supervised task named
it, and an attribute is whatever path its structured answer carried.

    store.py        the persisted, time-indexed store and its aggregation
    extract.py      structured answers (D-1) -> observations, generically
    watches.py      what the owner declared worth sampling, and the sampler
    techniques.py   series analysis as an open, governed registry (GM-09)
"""
