"""Patched arm of the lead-blocker experiment: one Write into state-47 steering.

Same trial as `lead_blocker.py` in every other respect, so `compare` sees only
the patch. The patch word and address come from the environment so a value can
be swept without editing a file:

    LB_ADDR=0x001B61E8 LB_WORD=0x3C0142C8 \
      python -m tools.madden_lab trial --spec experiments/lb_patch.py -n 4 --write

Levers in the state-47 steering (0x001B61A0), from lead-blocker-targeting.md:
  0x001B61A4/AC  cone half-angle, 60 deg (0x002aaaaa) -- two words (lui+ori)
  0x001B61E8     cone range / commit distance, 3.5 (lui 0x4060) -- one word
  0x001B6290/94  the 2x downfield velocity lead
"""
import os, dataclasses
from experiments.lead_blocker import build as _build
from tools.madden_lab.trial import Write


def _writes():
    spec = os.environ.get("LB_PATCH", "0x001B61E8=0x3C0142C8")
    why = os.environ.get("LB_WHY", "state-47 steering sweep")
    out = []
    for pair in spec.split(","):
        addr, word = pair.split("=")
        out.append(Write(int(addr, 16), int(word, 16), why))
    return tuple(out)


def build():
    t = _build()
    return dataclasses.replace(t, name="lead_blocker_patched", setup=_writes())
