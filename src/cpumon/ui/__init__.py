"""Presentation layer, split so that only the leaves know what a screen is.

    state.py     what the user has asked the display to do
    model.py     one frame as values: rows, kinds, metrics, numbers
    builder.py   fills a renderer's request from the history store
    help.py      the help page as content, with its scroll arithmetic
    renderer.py  the interface every render backend implements
    renders/     the backends; renders/cli is the ANSI terminal one

Everything above `renders/` is free of characters, colours and escape
sequences, which is what allows a second presentation to be added without
touching the loop, the collectors or the model.
"""

from __future__ import annotations
