#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""作りもののデータでジェスチャー検出を確かめる。 python3 tests/test_gestures.py"""
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from synth import Timeline  # noqa: E402

spec = importlib.util.spec_from_file_location("fgg", os.path.join(os.path.dirname(HERE), "frame-gaze-gestures.py"))
fgg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fgg)


def detect(tl, winks=True, **th):
    d = fgg.Detector(th, winks=winks)
    found = []
    for s in tl.samples():
        if s is None:
            d.reset()
        else:
            found += [(round(s[0] - 1000, 1), g) for g in d.feed(*s)]
    return [g for _, g in found]


class DetectorTest(unittest.TestCase):
    def test_double_long_blink(self):
        tl = Timeline().hold(2).blink(0.7).hold(0.6).blink(0.8).hold(2)
        # 2 回目の long_blink は cooldown（2 秒）で出ない。double_long_blink は出る
        self.assertEqual(detect(tl), ["long_blink", "double_long_blink"])

    def test_normal_blinks_are_ignored(self):
        tl = Timeline().hold(1)
        for _ in range(10):
            tl.blink(0.2).hold(1.5)  # ふつうのまばたき（0.2 秒）
        self.assertEqual(detect(tl), [])

    def test_long_blinks_too_far_apart(self):
        tl = Timeline().hold(1).blink(0.7).hold(4).blink(0.7).hold(1)
        self.assertEqual(detect(tl), ["long_blink", "long_blink"])

    def test_resting_eyes_is_not_a_blink(self):
        tl = Timeline().hold(1).blink(5).hold(1)  # 目を 5 秒休める
        self.assertEqual(detect(tl), [])

    def test_wink_left_and_right(self):
        tl = Timeline().hold(1).wink("left", 0.6).hold(3).wink("right", 0.6).hold(1)
        self.assertEqual(detect(tl), ["wink_left", "wink_right"])

    def test_wink_off_when_one_eye_tracked(self):
        tl = Timeline().hold(1).wink("left", 0.6).hold(1)
        self.assertEqual(detect(tl, winks=False), [])

    def test_wink_turning_into_blink_is_not_a_wink(self):
        tl = Timeline().hold(1).wink("left", 0.3).blink(0.3).hold(1)
        self.assertNotIn("wink_left", detect(tl))

    def test_look_up_hold(self):
        tl = Timeline().hold(1).hold(1.5, gaze=(0, 0.8)).hold(1)
        self.assertEqual(detect(tl), ["look_up"])

    def test_glance_up_is_ignored(self):
        tl = Timeline().hold(1).hold(0.5, gaze=(0, 0.8)).hold(1)
        self.assertEqual(detect(tl), [])

    def test_look_up_fires_once_while_held(self):
        tl = Timeline().hold(1).hold(6, gaze=(0, 0.8)).hold(1)
        self.assertEqual(detect(tl), ["look_up"])

    def test_hysteresis_keeps_jitter_from_refiring(self):
        tl = Timeline().hold(0.5).hold(1.2, gaze=(0, 0.8))
        for _ in range(5):
            tl.hold(0.3, gaze=(0, 0.5)).hold(1.2, gaze=(0, 0.8))  # 少し戻るだけ（0.55 - 0.15 より上）
        self.assertEqual(detect(tl), ["look_up"])

    def test_look_left_right_down(self):
        tl = (Timeline().hold(1).hold(1.5, gaze=(-0.8, 0)).hold(1)
              .hold(1.5, gaze=(0.8, 0)).hold(1).hold(1.5, gaze=(0, -0.8)).hold(1))
        self.assertEqual(detect(tl), ["look_left", "look_right", "look_down"])

    def test_tracking_lost_drops_half_done_gesture(self):
        tl = Timeline().hold(1).blink(0.3).hold(1, tracking=False).blink(0.3).hold(1)
        self.assertEqual(detect(tl), [])

    def test_cooldown(self):
        tl = Timeline().hold(1).wink("left", 0.5).hold(0.5).wink("left", 0.5).hold(1)
        self.assertEqual(detect(tl), ["wink_left"])


class ParseAndOscTest(unittest.TestCase):
    def test_parse_status(self):
        st = {"time": 5.0, "tracking": True, "sent": {"lids_vrcft": [0.7, 0.8], "lids": [0.7, 0.8], "gaze": [0.1, -0.2]}}
        self.assertEqual(fgg.parse_status(st), (5.0, [0.7, 0.8], [0.1, -0.2]))
        self.assertIsNone(fgg.parse_status({"time": 5.0, "tracking": False, "sent": None}))

    def test_osc_roundtrip(self):
        for args in [(True,), (False,), (0.5,), (3,), ("ok", True, False)]:
            m = fgg.osc_message("/a/b", *args)
            self.assertEqual(len(m) % 4, 0)
            (addr, got), = fgg.osc_decode(m)
            self.assertEqual(addr, "/a/b")
            self.assertEqual(got, list(args))

    def test_replay_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.jsonl")
            Timeline().hold(1).blink(0.7).hold(0.5).blink(0.7).hold(1).write_jsonl(p)
            cfg = fgg.load_config(None)
            found = fgg.replay(p, cfg, fgg.Actions(cfg, dry_run=True), winks=True)
            self.assertEqual([g for _, g in found], ["double_long_blink"])


class EndToEndTest(unittest.TestCase):
    """作りものの status.json を 10 回/秒書き換え、本体を動かし、localhost で OSC を受ける。"""

    def test_status_file_to_osc(self):
        with tempfile.TemporaryDirectory() as d:
            status = os.path.join(d, "status.json")
            rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            rx.bind(("127.0.0.1", 0))
            rx.settimeout(0.2)
            port = rx.getsockname()[1]
            tl = Timeline().hold(0.5).blink(0.7).hold(0.5).blink(0.7).hold(0.5).hold(1.5, gaze=(0, 0.8)).hold(1.5)

            def writer():
                for r in tl.rows:
                    r = dict(r, time=time.time(), target="198.51.100.7:9000")  # 文書用の IP（送り先には使わない）
                    with open(status + ".tmp", "w") as f:
                        json.dump(r, f)
                    os.replace(status + ".tmp", status)
                    time.sleep(0.1)

            th = threading.Thread(target=writer)
            th.start()
            rc = fgg.main(["--config", "/nonexistent", "--status-path", status,
                           "--target", f"127.0.0.1:{port}", "--duration", str(len(tl.rows) / 10 + 1)])
            th.join()
            got = []
            while True:
                try:
                    got += fgg.osc_decode(rx.recvfrom(4096)[0])
                except socket.timeout:
                    break
            self.assertEqual(rc, 0)
            self.assertIn(("/avatar/parameters/GazeToggle", [True]), got)
            self.assertIn(("/avatar/parameters/GazeLookUp", [True]), got)
            self.assertIn(("/avatar/parameters/GazeLookUp", [False]), got)  # パルスは戻る


if __name__ == "__main__":
    unittest.main(verbosity=2)
