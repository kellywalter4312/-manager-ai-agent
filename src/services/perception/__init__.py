"""What perception means over time (GM-03), the same for every backend:

    tracking.py   identity across frames - which region in this frame is the
                  same thing as which region in the last
    analysis.py   counts, tracks, arrivals, departures, stops; observations
                  recorded through the ecosystem's one entry point; a picture
                  of what was seen

Nothing here names a subject or knows a backend: a backend supplies regions,
and everything else follows from where they are and when.
"""
