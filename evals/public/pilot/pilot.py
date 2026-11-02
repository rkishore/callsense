"""Pilot: scan_text as deployed on 30 deepset rows and 30 English NotInject rows."""

import json
import logging
import random
import sys
from collections import Counter

from src.mcp_server import server

logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
assert server.get_jev_client() is not None, "no Jev key"
deepset = json.load(open(sys.argv[1]))
ni = [
    r
    for r in json.load(open(sys.argv[2]))
    if r["category"] != "Multilingual" and r["prompt"].isascii()
]
rng = random.Random(20261102)
ni_sample = [
    r
    for split in ("NotInject_one", "NotInject_two", "NotInject_three")
    for r in rng.sample([x for x in ni if x["split"] == split], 10)
]
items = [("deepset", "injection" if r["label"] else "benign", r["text"], "") for r in deepset]
items += [
    ("notinject", "benign", r["prompt"], ",".join(r["word_list"]) + " | " + r["category"])
    for r in ni_sample
]
out = []
for src, label, text, extra in items:
    res = server.scan_text(text)
    out.append(
        {
            "source": src,
            "label": label,
            "text": text,
            "extra": extra,
            "verdict": res.verdict,
            "jev_p": res.injection.jev_p,
            "patterns": res.injection.patterns_matched,
        }
    )
json.dump(out, open(sys.argv[3], "w"), indent=1)
for (src, label), n in sorted(Counter((o["source"], o["label"]) for o in out).items()):
    f = sum(1 for o in out if o["source"] == src and o["label"] == label and o["verdict"] != "pass")
    print(f"{src:9} {label:9} flagged {f}/{n}")
