# 開発者向けメモ

## 目のデータ

frameeyeosc が VRChat に送っている値を、そのまま正しい値として使います。

- 既定（`"source": "status"`）: frameeyeosc の状態ファイル `$XDG_RUNTIME_DIR/frameeyeosc/status.json`（frameeyeosc の README の「Status file」）を読むだけです。10 回/秒更新されます。使うのは `tracking` と `sent.lids_vrcft`（まぶた。0 = 閉じ、0.75 = ふつう、1 = 見開き）と `sent.gaze`（視線。-1〜1 が ±45°、+X が右、+Y が上）、それに `dominant_eye` と `target`
- `"source": "osc"`: frameeyeosc の OSC（`/avatar/parameters/FT/v2/EyeLidLeft` など）を `127.0.0.1:osc_listen_port` で受けます。ただし frameeyeosc は送り先を 1 つしか持てないので、これを使うと VRChat に直接は届かなくなります。frameeyeosc の設定を変える必要があるので、ふつうは `status` を使ってください

`status.json` は 10 回/秒なので、0.1 秒より短い動き（ふつうのまばたき）は数えられません。長いまばたき（0.5 秒以上）・ウインク・見続ける動きを使っています。

## 判定（`thresholds`）

| 項目 | 既定 | 意味 |
|---|---|---|
| `lid_closed_below` / `lid_open_above` | 0.2 / 0.45 | まぶたが閉じた・開いたと見なす値（あいだはヒステリシス） |
| `long_blink_min_s` / `long_blink_max_s` | 0.5 / 2.0 | 長いまばたきの長さ |
| `double_window_s` | 2.5 | 長いまばたき 2 回の間隔 |
| `wink_min_s` / `wink_max_s` | 0.4 / 2.0 | ウインクの長さ（もう片方は開いたまま） |
| `look_threshold` / `look_hysteresis` / `look_hold_s` | 0.55 / 0.15 / 1.0 | 見続ける: 視線がこれより外で、これだけ続いたら。戻ったらまた使える |
| `cooldown_s` | 2.0 | 同じジェスチャーを繰り返さない時間 |
| `max_gap_s` | 0.5 | データがこれ以上途切れたら、途中の動きは捨てる |

## 動作（`gestures.<名前>.actions`）

ジェスチャー: `double_long_blink`、`long_blink`、`wink_left`、`wink_right`、`look_up`、`look_down`、`look_left`、`look_right`

```json
{"type": "osc_pulse",  "address": "/avatar/parameters/GazeLookUp", "seconds": 0.5}
{"type": "osc_toggle", "address": "/avatar/parameters/GazeToggle"}
{"type": "osc",        "address": "/avatar/parameters/GazeMode", "value": 2}
{"type": "chatbox",    "text": "👀"}
{"type": "command",    "run": "notify-send 目で操作しました"}
```

チャットボックスは 10 秒に 1 回まで。コマンドは `bash -c` で動かし、環境変数 `FGG_GESTURE` にジェスチャー名が入ります（終わるのを待ちません）。

送り先: `"host": "auto"` なら frameeyeosc の送り先と同じ PC の `port`（9000）。

## コマンドラインオプション

`--config FILE` / `--target host:port` / `--status-path FILE` / `--replay FILE` / `--dry-run` / `--duration 秒` / `-v` / `--version`

## テスト

```sh
python3 tests/test_gestures.py      # 作りもののデータで判定・OSC・状態ファイルからの通し
python3 frame-gaze-gestures.py --replay tests/data/demo.jsonl --dry-run
```

`tests/synth.py` は status.json と同じ形の作りもののデータを作ります（`--replay` 用の .jsonl も書けます）。
