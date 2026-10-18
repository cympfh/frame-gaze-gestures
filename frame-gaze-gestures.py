#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""frame-gaze-gestures: Steam Frame の目の動き（長いまばたき 2 回、ウインク、上を見続ける…）を見つけて、
VRChat に OSC を送ったり、コマンドを動かしたりする。

目のデータは frameeyeosc（https://github.com/sasaken1102r/frameeyeosc）が出している値をそのまま使う:
  - 既定: frameeyeosc の状態ファイル $XDG_RUNTIME_DIR/frameeyeosc/status.json（10 回/秒、読むだけ）の "sent"
    = frameeyeosc が VRChat に送っている値（まぶた 0 = 閉じ、0.75 = ふつうに開いた状態、1 = 見開き。
      視線 -1〜1 = ±45°、+X が右、+Y が上）
  - OSC で受ける: frameeyeosc の送り先をこのツールに向けたとき（frameeyeosc の設定変更が要る。docs 参照）
Valve の共有メモリには触らない。Python 3 の標準ライブラリだけで動く。
"""
import argparse
import json
import os
import re
import shlex
import signal
import socket
import struct
import subprocess
import sys
import time

VERSION = "0.1.0"
CONFIG_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                           "frame-gaze-gestures", "config.json")
STATUS_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}",
                           "frameeyeosc", "status.json")
GESTURES = ("double_long_blink", "long_blink", "wink_left", "wink_right",
            "look_up", "look_down", "look_left", "look_right")
MIN_CHATBOX_INTERVAL_S = 10.0

DEFAULT_CONFIG = {
    "source": "status",          # "status"（frameeyeosc の状態ファイル）または "osc"（OSC で受ける）
    "status_path": None,         # null = $XDG_RUNTIME_DIR/frameeyeosc/status.json
    "osc_listen_port": 9100,     # source が "osc" のときに受けるポート
    "osc_prefix": "/FT",         # frameeyeosc の prefix と同じにする
    "host": "auto",              # 送り先。"auto" = frameeyeosc と同じ PC
    "port": 9000,
    "thresholds": {
        "lid_closed_below": 0.2,     # まぶたがこれより下なら「閉じた」（frameeyeosc の値で 0.75 がふつう）
        "lid_open_above": 0.45,      # これより上に戻ったら「開いた」（あいだはヒステリシス）
        "long_blink_min_s": 0.5,     # 長いまばたき: 両目をこの秒数以上つぶる
        "long_blink_max_s": 2.0,     #   これより長いのは数えない（目を休めているだけ）
        "double_window_s": 2.5,      # 長いまばたき 2 回をこの秒数以内に
        "wink_min_s": 0.4,           # ウインク: 片目だけをこの秒数以上つぶる
        "wink_max_s": 2.0,
        "look_threshold": 0.55,      # 視線がこれより外（-1〜1、±45° が ±1）
        "look_hysteresis": 0.15,     # threshold からこれだけ戻ったら、もう一度使える
        "look_hold_s": 1.0,          # この秒数見続けたら
        "cooldown_s": 2.0,           # 同じジェスチャーは、この秒数のあいだ繰り返さない
        "max_gap_s": 0.5             # データがこれ以上途切れたら、途中の動きは捨てる
    },
    "gestures": {
        "double_long_blink": {"enabled": True, "actions": [
            {"type": "osc_toggle", "address": "/avatar/parameters/GazeToggle"}]},
        "long_blink": {"enabled": False, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeLongBlink", "seconds": 0.5}]},
        "wink_left": {"enabled": True, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeWinkLeft", "seconds": 0.5}]},
        "wink_right": {"enabled": True, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeWinkRight", "seconds": 0.5}]},
        "look_up": {"enabled": True, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeLookUp", "seconds": 0.5}]},
        "look_down": {"enabled": False, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeLookDown", "seconds": 0.5}]},
        "look_left": {"enabled": False, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeLookLeft", "seconds": 0.5}]},
        "look_right": {"enabled": False, "actions": [
            {"type": "osc_pulse", "address": "/avatar/parameters/GazeLookRight", "seconds": 0.5}]}
    },
    "log_every_s": 60.0,
}


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


# ---------------------------------------------------------------- OSC
def _pad(b):
    return b + b"\0" * (4 - len(b) % 4)


def osc_message(address, *args):
    tags, data = ",", b""
    for v in args:
        if v is True:
            tags += "T"
        elif v is False:
            tags += "F"
        elif isinstance(v, float):
            tags += "f"
            data += struct.pack(">f", v)
        elif isinstance(v, int):
            tags += "i"
            data += struct.pack(">i", v)
        elif isinstance(v, str):
            tags += "s"
            data += _pad(v.encode("utf-8"))
        else:
            raise TypeError(f"OSC にできない型: {type(v)}")
    return _pad(address.encode()) + _pad(tags.encode()) + data


def _osc_str(buf, i):
    end = buf.index(b"\0", i)
    return buf[i:end].decode("utf-8", "replace"), (end + 4) & ~3


def osc_decode(buf):
    """OSC メッセージを (address, [args]) に。bundle は中身を順に返す。"""
    if buf.startswith(b"#bundle\0"):
        out, i = [], 16
        while i + 4 <= len(buf):
            n = struct.unpack(">i", buf[i:i + 4])[0]
            out += osc_decode(buf[i + 4:i + 4 + n])
            i += 4 + n
        return out
    addr, i = _osc_str(buf, 0)
    tags, i = _osc_str(buf, i)
    args = []
    for t in tags[1:]:
        if t == "f":
            args.append(struct.unpack(">f", buf[i:i + 4])[0]); i += 4
        elif t == "i":
            args.append(struct.unpack(">i", buf[i:i + 4])[0]); i += 4
        elif t == "s":
            s, i = _osc_str(buf, i); args.append(s)
        elif t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
    return [(addr, args)]


# ---------------------------------------------------------------- ジェスチャー検出
class Detector:
    """1 サンプルずつ (時刻, [左まぶた, 右まぶた], [視線X, 視線Y]) を受け取り、見つけたジェスチャー名を返す。"""

    def __init__(self, thresholds, winks=True):
        self.th = dict(DEFAULT_CONFIG["thresholds"], **(thresholds or {}))
        self.winks = winks
        self.last_fired = {}
        self.reset()

    def reset(self):
        self.last_t = None
        self.closed = [False, False]
        self.both_since = None       # 両目を閉じはじめた時刻
        self.last_long_end = None    # 前の長いまばたきが終わった時刻
        self.wink = None             # (目, 開始時刻) 片目だけ閉じているあいだ
        self.wink_spoiled = False    # ウインクの途中でもう片方も閉じた
        self.look = {d: {"since": None, "fired": False} for d in ("up", "down", "left", "right")}

    def _fire(self, name, t, out):
        if t - self.last_fired.get(name, -1e9) >= self.th["cooldown_s"]:
            self.last_fired[name] = t
            out.append(name)

    def feed(self, t, lids, gaze):
        th, out = self.th, []
        if self.last_t is not None and (t - self.last_t > th["max_gap_s"] or t < self.last_t):
            fired = self.last_fired
            self.reset()
            self.last_fired = fired
        self.last_t = t

        # まぶた（ヒステリシス付き）
        for e in (0, 1):
            if self.closed[e] and lids[e] > th["lid_open_above"]:
                self.closed[e] = False
            elif not self.closed[e] and lids[e] < th["lid_closed_below"]:
                self.closed[e] = True
        both = self.closed[0] and self.closed[1]
        one = self.closed[0] != self.closed[1]
        opened = not self.closed[0] and not self.closed[1]

        # 両目のまばたき
        if both and self.both_since is None:
            self.both_since = t
        if not both and self.both_since is not None:
            dur = t - self.both_since
            self.both_since = None
            if th["long_blink_min_s"] <= dur <= th["long_blink_max_s"]:
                self._fire("long_blink", t, out)
                if self.last_long_end is not None and t - self.last_long_end <= th["double_window_s"]:
                    self._fire("double_long_blink", t, out)
                    self.last_long_end = None
                else:
                    self.last_long_end = t

        # ウインク（片目だけ。もう片方は開いたまま）
        if self.winks:
            if one and self.wink is None and not self.wink_spoiled:
                self.wink = (0 if self.closed[0] else 1, t)
            if both and self.wink is not None:
                self.wink, self.wink_spoiled = None, True
            if opened:
                if self.wink is not None:
                    dur = t - self.wink[1]
                    if th["wink_min_s"] <= dur <= th["wink_max_s"]:
                        self._fire("wink_left" if self.wink[0] == 0 else "wink_right", t, out)
                self.wink, self.wink_spoiled = None, False

        # 見続ける（目を開いているときの視線だけ）
        gx, gy = gaze
        values = {"up": gy, "down": -gy, "right": gx, "left": -gx}
        for d, v in values.items():
            st = self.look[d]
            if opened and v > th["look_threshold"]:
                if st["since"] is None:
                    st["since"] = t
                if not st["fired"] and t - st["since"] >= th["look_hold_s"]:
                    st["fired"] = True
                    self._fire("look_" + d, t, out)
            elif not opened or v < th["look_threshold"] - th["look_hysteresis"]:
                st["since"], st["fired"] = None, False
        return out


# ---------------------------------------------------------------- データの出どころ
class StatusSource:
    """frameeyeosc の status.json（10 回/秒）を読む。読むだけで、何も書かない。"""

    def __init__(self, path):
        self.path = path
        self.last_time = None
        self.info = {}

    def poll(self):
        try:
            with open(self.path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return "nodata", None
        t = st.get("time")
        if t is None or t == self.last_time:
            return "same", None
        self.last_time = t
        self.info = {"target": st.get("target"), "dominant_eye": st.get("dominant_eye"),
                     "sending": st.get("sending")}
        if time.time() - t > 2.0:
            return "stale", None          # frameeyeosc が止まっている
        return "sample", parse_status(st)


def parse_status(st):
    """status.json（またはその形の 1 行）から (時刻, まぶた, 視線) を取り出す。無ければ None。"""
    sent = st.get("sent")
    if not st.get("tracking") or not sent:
        return None
    lids, gaze = sent.get("lids_vrcft") or sent.get("lids"), sent.get("gaze")
    if not lids or not gaze:
        return None
    return float(st["time"]), [float(lids[0]), float(lids[1])], [float(gaze[0]), float(gaze[1])]


class OscSource:
    """frameeyeosc の OSC（VRChat 向け、VRCFT の名前）を受ける。"""

    def __init__(self, port, prefix):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.setblocking(False)
        p = "/avatar/parameters" + ("/" + prefix.strip("/") if prefix.strip("/") else "")
        self.names = {p + "/v2/EyeLidLeft": ("lid", 0), p + "/v2/EyeLidRight": ("lid", 1),
                      p + "/v2/EyeX": ("gaze", 0), p + "/v2/EyeY": ("gaze", 1)}
        self.active_name = p + "/EyeTrackingActive"
        self.lids, self.gaze, self.active, self.fresh = [0.75, 0.75], [0.0, 0.0], True, False
        self.info = {}

    def poll(self):
        got = False
        while True:
            try:
                buf, _ = self.sock.recvfrom(4096)
            except BlockingIOError:
                break
            for addr, args in osc_decode(buf):
                if addr == self.active_name and args:
                    self.active = bool(args[0])
                elif addr in self.names and args:
                    kind, i = self.names[addr]
                    (self.lids if kind == "lid" else self.gaze)[i] = float(args[0])
                    got = True
        if not got:
            return "same", None
        if not self.active:
            return "sample", None
        return "sample", (time.time(), list(self.lids), list(self.gaze))


# ---------------------------------------------------------------- 送り先と動作
def frameeyeosc_target(status_info):
    """frameeyeosc が送っている PC（status.json の target、次に env の --target）。(host, 理由) か None。"""
    t = status_info.get("target") if status_info else None
    if t and ":" in t:
        return t.rsplit(":", 1)[0].strip("[]"), "frameeyeosc の送り先"
    env = ""
    try:
        with open(os.path.expanduser("~/.config/frameeyeosc/env")) as f:
            env = f.read()
    except OSError:
        pass
    for line in env.splitlines():
        m = re.match(r"\s*FRAMEEYEOSC_ARGS=(.*)", line)
        if m:
            try:
                args = shlex.split(" ".join(shlex.split(m.group(1))))
            except ValueError:
                continue
            for i, a in enumerate(args):
                if a == "--target" and i + 1 < len(args) and ":" in args[i + 1]:
                    return args[i + 1].rsplit(":", 1)[0], "frameeyeosc の env (--target)"
    return None


class Actions:
    def __init__(self, cfg, dry_run=False, verbose=False):
        self.cfg, self.dry_run, self.verbose = cfg, dry_run, verbose
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.target = None
        self.toggles = {}
        self.pending = []           # (時刻, address) パルスを False に戻す予定
        self.last_chat = -1e9
        self.sent = 0

    def set_target(self, host, port, why):
        if self.target != (host, port):
            self.target = (host, port)
            log(f"送り先: {host}:{port}（{why}）")

    def send(self, address, *args):
        if self.verbose or self.dry_run:
            log("  ->", address, *args)
        if self.dry_run or not self.target:
            return
        try:
            self.sock.sendto(osc_message(address, *args), self.target)
            self.sent += 1
        except OSError as e:
            log(f"送信エラー: {e}")

    def run(self, gesture, now):
        g = self.cfg["gestures"].get(gesture) or {}
        for act in g.get("actions", []):
            kind = act.get("type")
            if kind == "osc_pulse":
                self.send(act["address"], True)
                self.pending.append((now + float(act.get("seconds", 0.5)), act["address"]))
            elif kind == "osc_toggle":
                v = not self.toggles.get(act["address"], False)
                self.toggles[act["address"]] = v
                self.send(act["address"], v)
            elif kind == "osc":
                self.send(act["address"], act["value"])
            elif kind == "chatbox":
                if now - self.last_chat >= MIN_CHATBOX_INTERVAL_S:
                    self.send("/chatbox/input", str(act.get("text", gesture))[:144], True, False)
                    self.last_chat = now
            elif kind == "command":
                log(f"  コマンド: {act['run']}")
                if not self.dry_run:
                    env = dict(os.environ, FGG_GESTURE=gesture)
                    subprocess.Popen(act["run"], shell=True, env=env, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                log(f"  知らない動作 {kind!r}（無視）")

    def tick(self, now):
        due = [p for p in self.pending if p[0] <= now]
        self.pending = [p for p in self.pending if p[0] > now]
        for _, address in due:
            self.send(address, False)


# ---------------------------------------------------------------- 設定と本体
def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if k not in cfg:
                log(f"警告: 知らない設定項目 {k!r} は無視します")
            elif k == "thresholds":
                cfg[k].update(v)
            elif k == "gestures":
                for name, g in v.items():
                    if name not in GESTURES:
                        log(f"警告: 知らないジェスチャー {name!r} は無視します")
                        continue
                    cfg[k][name] = dict(cfg[k][name], **g)
            else:
                cfg[k] = v
    return cfg


def replay(path, cfg, actions, winks):
    """status.json の形の行を並べた .jsonl を流して、見つかったジェスチャーを返す（テスト・調整用）。"""
    det = Detector(cfg["thresholds"], winks=winks)
    found = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            st = json.loads(line)
            s = parse_status(st)
            if s is None:
                det.reset()
                continue
            for g in det.feed(*s):
                if cfg["gestures"].get(g, {}).get("enabled"):
                    log(f"{s[0]:.1f}s: {g}")
                    found.append((round(s[0], 2), g))
                    actions.run(g, s[0])
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description="Steam Frame の目のジェスチャーで OSC やコマンドを動かす")
    ap.add_argument("--config", default=CONFIG_PATH, help=f"設定ファイル（既定: {CONFIG_PATH}）")
    ap.add_argument("--target", help="送り先 host:port（設定より優先）")
    ap.add_argument("--status-path", help="frameeyeosc の status.json の場所")
    ap.add_argument("--replay", metavar="FILE", help="記録（.jsonl）を流して、見つかったものを表示して終わる")
    ap.add_argument("--dry-run", action="store_true", help="送らない・コマンドを動かさない")
    ap.add_argument("--duration", type=float, default=0, help="この秒数で終わる（テスト用）")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"frame-gaze-gestures {VERSION}")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    actions = Actions(cfg, dry_run=a.dry_run, verbose=a.verbose)
    if a.target:
        h, p = a.target.rsplit(":", 1)
        actions.set_target(h, int(p), "--target")

    if a.replay:
        found = replay(a.replay, cfg, actions, winks=True)
        log(f"見つかったジェスチャー: {len(found)} 個")
        return 0

    if cfg["source"] == "osc":
        src = OscSource(int(cfg["osc_listen_port"]), cfg["osc_prefix"])
        log(f"frame-gaze-gestures {VERSION}: OSC を 127.0.0.1:{cfg['osc_listen_port']} で受けます")
    else:
        path = a.status_path or cfg["status_path"] or STATUS_PATH
        src = StatusSource(path)
        log(f"frame-gaze-gestures {VERSION}: frameeyeosc の状態ファイル {path} を読みます（読むだけ）")
    enabled = [g for g in GESTURES if cfg["gestures"].get(g, {}).get("enabled")]
    log(f"使うジェスチャー: {', '.join(enabled) or 'なし'}{' (dry-run)' if a.dry_run else ''}")

    det = Detector(cfg["thresholds"])
    stop = {"v": False}

    def on_signal(*_):
        stop["v"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    t0 = last_log = time.monotonic()
    samples, last_state, dominant_noted = 0, None, None
    while not stop["v"]:
        now = time.monotonic()
        state, s = src.poll()
        info = src.info
        # 送り先
        if not a.target:
            if cfg["host"] != "auto":
                actions.set_target(cfg["host"], int(cfg["port"]), "設定")
            else:
                ft = frameeyeosc_target(info)
                if ft:
                    actions.set_target(ft[0], int(cfg["port"]), ft[1])
                elif actions.target is None:
                    actions.set_target("127.0.0.1", int(cfg["port"]), "既定")
        # Frame が片目しか追っていないときは、左右の値が同じになるのでウインクは使えない
        dom = info.get("dominant_eye")
        if dom != dominant_noted:
            dominant_noted = dom
            det.winks = dom is None
            if dom:
                log(f"Frame が片目（{dom}）だけを追う設定なので、ウインクは使いません")
        if state in ("nodata", "stale") and state != last_state:
            log("frameeyeosc の状態ファイルが読めない／更新されていません。frameeyeosc が動くのを待ちます")
            det.reset()
        last_state = state
        if state == "sample":
            if s is None:
                det.reset()
            else:
                samples += 1
                for g in det.feed(*s):
                    if cfg["gestures"].get(g, {}).get("enabled"):
                        log(f"ジェスチャー: {g}")
                        actions.run(g, now)
        actions.tick(now)
        if now - last_log >= cfg["log_every_s"]:
            log(f"状態: サンプル {samples} 個 / 送信 {actions.sent} パケット")
            last_log = now
        if a.duration and now - t0 >= a.duration:
            break
        time.sleep(0.02)
    actions.tick(float("inf"))      # 押しっぱなしのパルスを戻してから終わる
    log(f"終了: サンプル {samples} 個 / 送信 {actions.sent} パケット")
    return 0


if __name__ == "__main__":
    sys.exit(main())
