# frame-gaze-gestures

Steam Frame で **目の動きをスイッチにする** ツールです。
「ゆっくり 2 回まばたき」でアバターの衣装を切り替えたり、「上を 1 秒見る」でエフェクトを出したりできます。手はふさがったままで大丈夫です。

```
15:24:09 ジェスチャー: double_long_blink   → GazeToggle = true（もう一度で false）
15:24:12 ジェスチャー: look_up             → GazeLookUp を 0.5 秒だけ true
```

| 目の動き | 名前 | 最初の設定で送るもの |
|---|---|---|
| 0.5〜2 秒のまばたきを 2 回 | `double_long_blink` | `GazeToggle`（押すたびにオン・オフ） |
| 上を 1 秒見続ける | `look_up` | `GazeLookUp`（0.5 秒だけオン） |
| 左目・右目だけでウインク | `wink_left` / `wink_right` | `GazeWinkLeft` / `GazeWinkRight`（0.5 秒だけオン） |

[frameeyeosc](https://github.com/sasaken1102r/frameeyeosc) が必要です（目のデータは frameeyeosc から受け取ります）。

## インストール

Frame の Konsole に貼り付けて Enter:

```sh
curl -fsSL https://raw.githubusercontent.com/cympfh/frame-gaze-gestures/main/install.sh | bash
```

## オン・オフ

```sh
systemctl --user enable --now frame-gaze-gestures    # オン（次からは SteamVR と一緒に起動）
systemctl --user disable --now frame-gaze-gestures   # オフ
```

## VRChat の準備

- アバターの Expression Parameters に `GazeToggle`、`GazeLookUp`、`GazeWinkLeft`、`GazeWinkRight`（どれも Bool）を追加して、アニメーターで使う
- VRChat のアクションメニュー → オプション → OSC → 有効
- パラメーターを足したアバターが反応しないときは、同じメニューの「Reset Config」を押す
- 送り先の PC は frameeyeosc と同じです

Frame の設定で「利き目だけを追跡」がオンだと、左右の目が同じ値になるので、ウインクは使えません（自動でオフになります）。

## アンインストール

```sh
curl -fsSL https://raw.githubusercontent.com/cympfh/frame-gaze-gestures/main/install.sh | bash -s -- --uninstall
```

外したファイルは消さずに `~/.local/share/frame-gaze-gestures-uninstalled/` へ移します。設定ファイルは残ります。

<details>
<summary>くわしい情報（設定・開発者向け）</summary>

- 設定ファイル: `~/.config/frame-gaze-gestures/config.json`（初回にサンプルからコピー）。ジェスチャーごとに、送る OSC・チャットボックス・好きなコマンドを決められます。項目は [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
- ログ: `journalctl --user -u frame-gaze-gestures -f`
- 被らずに試す: `python3 ~/.local/opt/frame-gaze-gestures/frame-gaze-gestures.py --replay 記録.jsonl --dry-run`
- Python 3 の標準ライブラリだけで動き、sudo は使いません。Valve の共有メモリには触りません

</details>

ほかの Steam Frame 用ツール: [frame-tools](https://github.com/cympfh/frame-tools)
