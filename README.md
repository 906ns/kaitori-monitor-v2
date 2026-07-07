# kaitori-monitor-v2 — 買取商店 価格監視 Discord Bot

[買取商店](https://www.kaitorishouten-co.jp/) の買取価格を監視し、Discord に通知する bot。

> **状態: 実装済み(動作確認: スクレイパーは実サイトで検証済み。Discord bot はラズパイ上で bot トークンを設定して起動する)**

## クイックスタート(ラズパイでのセットアップ)

```bash
git clone https://github.com/906ns/kaitori-monitor-v2.git
cd kaitori-monitor-v2
python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env                                # 編集: botトークンとチャンネルID
cp config/targets.yaml.example config/targets.yaml  # 編集: 監視対象の初期リスト

venv/bin/python -m kaitori_monitor                  # 起動
```

Discord bot は [Discord Developer Portal](https://discord.com/developers/applications) で作成し、
`applications.commands` と `bot` スコープ(Send Messages 権限)でサーバーに招待しておく。

### Discord コマンド

| コマンド | 動作 |
|---|---|
| `/add <JANまたは商品名>` | 監視対象に追加。複数ヒット時は選択メニューが出る |
| `/remove <番号またはJAN>` | 監視対象から削除(番号は `/list` 表示のもの) |
| `/list` | 監視対象の一覧と前回価格 |
| `/price [キーワード]` | その場で価格照会。省略時は全監視対象、指定時は検索(未登録商品も可) |
| `/settime <HH:MM>` | 毎日の通知時刻を変更(JST、既定 09:00) |

### スクレイパー単体での確認

```bash
venv/bin/python -m kaitori_monitor.scraper 4549995536447
# [21140] iPhone 16 Pro Max 256GB 金  JAN:4549995536447  新品:188,000円  中古:137,000円
```

---

## 1. 確定した要件

| 項目 | 内容 |
|---|---|
| 監視対象サイト | https://www.kaitorishouten-co.jp/ (買取商店) |
| 入出力 | Discord bot(スラッシュコマンド + 定期通知) |
| 定期通知 | 1日1回、**全監視対象の現在の買取価格一覧** を通知 |
| 任意実行 | Discord コマンドでいつでも現在価格を照会できる |
| 監視対象の指定 | **JANコード** または **商品名** で登録 |
| 対象の管理方法 | 設定ファイル + Discord コマンドの**両方** |
| 監視する価格 | 新品(対象商品は新品価格のみの想定) |
| 価格の取得方法 | サイト内検索(JAN/商品名で検索し結果ページから価格を取得) |
| 実行環境 | Raspberry Pi(常時稼働、systemd サービスとして運用) |
| 技術スタック | Python |
| 開発PCへの環境構築 | しない(実装・実行はラズパイ側で行う) |

## 2. アーキテクチャ

```
┌─────────────── Raspberry Pi ───────────────┐
│  systemd service: kaitori-monitor           │
│                                             │
│  ┌───────────┐   ┌────────────┐             │
│  │ discord.py │←→│ スケジューラ │ (毎日 HH:MM) │
│  │  bot本体   │   │ (内蔵tasks) │             │
│  └─────┬─────┘   └─────┬──────┘             │
│        │               │                    │
│        ▼               ▼                    │
│  ┌─────────────────────────┐                │
│  │ スクレイパー (サイト内検索) │→ 買取商店サイト │
│  └───────────┬─────────────┘                │
│              ▼                              │
│  ┌─────────────────────────┐                │
│  │ SQLite (対象・価格履歴)    │                │
│  │ targets.yaml (初期設定)   │                │
│  └─────────────────────────┘                │
└─────────────────────────────────────────────┘
```

### 使用ライブラリ(予定)

- `discord.py` — bot 本体・スラッシュコマンド・定期タスク(`discord.ext.tasks`)
- `httpx` または `requests` — HTTP クライアント
- `beautifulsoup4` — HTML パース
- `PyYAML` — 設定ファイル読み込み
- `sqlite3`(標準ライブラリ)— 監視対象と価格履歴の保存

外部のジョブスケジューラ(cron 等)は使わず、bot プロセス内の `discord.ext.tasks` で毎日の通知時刻を管理する(コマンド応答と定期通知を1プロセスで完結させるため)。

## 3. Discord コマンド設計

| コマンド | 動作 |
|---|---|
| `/add <JANまたは商品名>` | 監視対象に追加。商品名で検索結果が複数ある場合は候補を提示して選択させ、確定した商品を保存する |
| `/remove <対象>` | 監視対象から削除(`/list` の番号または JAN 指定) |
| `/list` | 現在の監視対象一覧を表示 |
| `/price [対象]` | **任意タイミングの照会**。指定対象(省略時は全対象)の現在価格をその場で取得して表示 |
| `/settime <HH:MM>` | 毎日の定期通知時刻を変更(既定 09:00 JST) |

### 定期通知(1日1回)

- 登録済み全対象の「商品名 / JAN / 現在の買取価格 / 前回比(参考表示)」を Embed 形式で一覧通知
- 取得に失敗した商品は「取得失敗」と明示して通知に含める(黙って欠落させない)

## 4. 監視対象の管理(設定ファイル + コマンドの両立)

- `config/targets.yaml` に初期対象を記述(JAN または商品名のリスト)
- 起動時に YAML を読み込み SQLite に同期。以後の `/add` `/remove` は SQLite に反映
- 実行時の正: SQLite。YAML は「初期投入・バックアップ用」と位置付け、コマンドでの変更を YAML に書き戻すかは実装時に選択(既定: 書き戻さない)

```yaml
# config/targets.yaml の例
notify_time: "09:00"        # JST
discord_channel_id: 123456789012345678
targets:
  - jan: "4902370542912"
  - name: "Nintendo Switch 2 本体"
```

## 5. スクレイピング設計(実サイトで確認済み)

2026-07-07 に実サイトへアクセスして以下を確認済み。

### 検索 API

サイト内検索は AJAX の POST リクエスト。ブラウザを使わず HTTP クライアントだけで取得できる。

```
POST https://www.kaitorishouten-co.jp/products/list/keyword
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest   # 必須。無いと 404 が返る
User-Agent: (一般的なブラウザの UA を明示)   # 無いと 403 の可能性

name=<JANコードまたは商品名>&page_type=1
```

- 検索ボックスは「商品名・JANコード入力」対応を公式に明記
- **JAN コード検索は一意に 1 件ヒットすることを確認済み**(例: `4549995536447` → iPhone 16 Pro Max 256GB 金)
- 商品名検索は部分一致で複数ヒット(例: 「iPhone 16」で多数)

### 結果 HTML の構造

商品 1 件 = `<tr id="ex-product-{クラスID}" class="price_list_item class_list_{商品ID}">`

| 抽出項目 | セレクタ / 位置 |
|---|---|
| 商品名 | 2番目の `td.align-middle` 直下のテキスト |
| JAN | `span.product-code-default`(`JAN:` ラベルの次の span) |
| 新品買取価格 | 1つ目の `td.col-product-price` 内 `div.item-price`(例: `188,000円`) |
| 中古買取価格 | 2つ目の `td.col-product-price` 内 `div.item-price`(参考。監視対象は新品のみ) |
| 減額条件 | `div.biko_main`(開封済 -18,500円 等。通知には含めない) |

- 商品の一意キーは `tr` の id に含まれる**商品クラス ID**(例: `21140`)を使用
- 価格は「新品」のみ記録(要件)。中古価格もHTML上にあるため、将来の拡張は容易

### 登録フロー

- JAN 登録: 検索して一意にヒットした商品を自動確定
- 商品名登録: 複数ヒット時は `/add` の応答で候補一覧を提示 → ユーザーが選択 → 商品クラス ID を保存し、以後はそれで追跡

### アクセスマナー・注意点

- リクエスト間に 2〜3 秒のウェイト。アクセスは1日1回の巡回 + 手動照会のみの低頻度
- 一般的なブラウザ User-Agent を明示(データセンター系のデフォルト UA は 403 になる場合を確認)
- 取得失敗(構造変更・タイムアウト等)はリトライ(最大3回、指数バックオフ)の上、失敗として通知に含める
- 価格表示の div に `encrypt-price` クラスがあり、条件によって価格が難読化される可能性がある。実装時に平文(`plain-price`)でないケースが出たら要調査

## 6. データ保存(SQLite)

- `targets(id, jan, name, product_key, added_at)` — 監視対象
- `price_history(id, target_id, price, fetched_at, status)` — 取得ごとの価格履歴(前回比表示・将来のグラフ化に利用)

## 7. ラズパイでの常時稼働(systemd)

クイックスタートの手順で起動確認したあと、自動起動を設定する:

```bash
sudo cp deploy/kaitori-monitor.service /etc/systemd/system/
# ユニット内の User= と WorkingDirectory= を自分の環境に合わせて編集
sudo systemctl daemon-reload
sudo systemctl enable --now kaitori-monitor
journalctl -u kaitori-monitor -f   # ログ確認
```

## 8. プロジェクト構成

```
kaitori_monitor/
├── __main__.py   # エントリポイント (python -m kaitori_monitor)
├── config.py     # .env と targets.yaml の読み込み
├── scraper.py    # サイト内検索スクレイパー(単体CLIあり)
├── storage.py    # SQLite(監視対象・価格履歴・設定)
└── bot.py        # Discord bot(スラッシュコマンド+毎日の定期通知)
tests/            # パーサのユニットテスト
deploy/           # systemd ユニット
config/           # targets.yaml.example
```

## 9. 進捗

1. ~~サイト構造の調査~~ ✅ (2026-07-07)
2. ~~スクレイパー実装・実サイトで検証~~ ✅ (JAN検索・商品名検索・価格抽出を確認)
3. ~~Discord bot(コマンド + 定期通知)実装~~ ✅ (ユニットテスト・インポート確認済み)
4. **(次のステップ)** ラズパイにデプロイし、bot トークンを設定して実運用確認
