# SPDX-License-Identifier: MIT
"""作りものの目のデータ（frameeyeosc の status.json と同じ形、10 回/秒）を作る。

まぶたは frameeyeosc の値の目盛り（0 = 閉じ、0.75 = ふつう）、視線は -1〜1（+Y が上）。
"""
import json
import random

RATE = 10.0  # frameeyeosc は status.json を 10 回/秒書く


class Timeline:
    def __init__(self, start=1000.0, seed=1):
        self.t = start
        self.rows = []
        self.rng = random.Random(seed)

    def hold(self, seconds, lids=(0.75, 0.75), gaze=(0.0, 0.0), tracking=True, noise=0.02):
        for _ in range(int(round(seconds * RATE))):
            n = lambda: self.rng.uniform(-noise, noise)
            row = {"time": round(self.t, 3), "tracking": tracking, "dominant_eye": None}
            if tracking:
                row["sent"] = {"lids_vrcft": [max(0.0, lids[0] + n()), max(0.0, lids[1] + n())],
                               "gaze": [gaze[0] + n(), gaze[1] + n()]}
            else:
                row["sent"] = None
            self.rows.append(row)
            self.t += 1 / RATE
        return self

    def blink(self, closed_s):
        return self.hold(closed_s, lids=(0.0, 0.0))

    def wink(self, eye, closed_s):
        return self.hold(closed_s, lids=(0.0, 0.8) if eye == "left" else (0.8, 0.0))

    def samples(self):
        for r in self.rows:
            if r["tracking"]:
                yield r["time"], r["sent"]["lids_vrcft"], r["sent"]["gaze"]
            else:
                yield None

    def write_jsonl(self, path):
        with open(path, "w") as f:
            for r in self.rows:
                f.write(json.dumps(r) + "\n")
