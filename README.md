# Bitget XAUUSDT / XAUTUSDT 先物ペアトレードボット(本番運用版 / cron方式)

Bitget先物(USDT-M Perpetual)の **XAUUSDT** と **XAUTUSDT** の価格差を監視し、
一定以上の乖離が生じたときに割高な方をショート・割安な方をロングする
市場中立型ペアトレードボットです。

このバージョンは **共用レンタルサーバー(ラッコサーバー等)のcronで1分ごとに
1回だけ起動・終了する**方式で動きます。常駐プロセス(while Trueで回り続ける方式)
ではありません。理由は以下の通りです。

- 共用サーバーは「短時間で終わる処理をたくさん捌く」前提で設計されており、
  多くのホスティング事業者(ラッコサーバー含む)は利用規約で
  「サーバーに継続的に高負荷をかけ続けるプログラムの設置」を禁止しています
- cronによる短時間処理の定期実行は、まさにレンタルサーバーが公式に提供している
  正規の機能であり、規約上も安全です

そのため、状態(保有ポジション・当日取引回数など)はメモリではなく
`state.json` というファイルに毎回保存し、次回のcron実行時に読み込んで
引き継ぐ設計になっています。

## ⚠️ 必ず読んでください:リスクと制約

- **完全なマーケットニュートラルではありません。** XAUUSDT(ゴールド指数)と
  XAUTUSDT(XAUT連動)は算出方法が異なるため、微妙なトラッキング差が生じ得ます。
- **レバレッジ商品です。** isolatedモードでも、急変動時にはロング・ショート
  どちらの脚も個別に証拠金不足・清算(liquidation)のリスクがあります。
- **ファンディングレート**の受け払い差は戦略判断に織り込んでいません。
- **レッグリスク**: 2脚は逐次発注のため、片方だけ約定してもう片方が失敗することが
  あります。失敗時は残った脚を即座に手仕舞いしますが、その間の価格変動リスクは
  ゼロにはできません。
- **cronは1分間隔での実行です。** 1分未満で開いて閉じるような短命なスプレッドは
  検知できない場合があります。エントリー精度は常駐ループ版より下がります。
  一方、保有中のポジションの決済判定(利確・損切り・時間切れ)は1分間隔でも
  実用上十分機能します。
- 暗号資産・レバレッジ取引は元本を毀損するリスクがあります。自己責任で、
  必ず少額・低レバレッジ・`DRY_RUN=true`での検証を経てから実運用してください。
  本コードは教育・研究目的の参考実装であり、投資助言ではありません。

## アーキテクチャ

```
GitHub(ソース管理)
   │  git push / git pull
   ▼
ラッコサーバー(SSH)
   │
   ├─ venv (Python仮想環境)
   ├─ .env (APIキー・設定、Gitには含めない)
   ├─ state.json (ポジション等の状態、cron実行ごとに更新)
   ├─ bot.log (アプリケーションログ、ローテーション付き)
   ├─ trades.csv (取引履歴)
   └─ cron (cPanel「Cronジョブ」機能、1分ごとに run_once.py を起動)
```

## ファイル構成

| ファイル | 役割 |
|---|---|
| `bitget_client.py` | Bitget先物(Mix API v2) REST クライアント |
| `config.py` | `.env` から設定を読み込む |
| `state_store.py` | ポジション等の状態をJSONファイルに保存・復元 |
| `strategy.py` | エントリー/決済の判定ロジック本体 |
| `run_once.py` | cronから呼ばれるエントリーポイント(ロック制御込み) |
| `.env.example` | 設定サンプル(これをコピーして`.env`を作る) |
| `.gitignore` | `.env`や状態ファイルをGit管理対象から除外 |
| `requirements.txt` | 依存パッケージ |

## セットアップ手順

### 1. GitHubにリポジトリを作成してコードを push

手元の環境(このチャットからダウンロードしたファイル一式)で:

```bash
cd bitget_arb_bot_prod
git init
git add .
git commit -m "Initial commit: Bitget XAU/XAUT arb bot (cron version)"
```

GitHub上で新しいリポジトリを作成(**Private推奨**。取引ロジックやパラメータを
公開したくない場合)し、案内される手順で push します。

```bash
git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
git branch -M main
git push -u origin main
```

### 2. Bitget側の事前設定

- 先物(USDT-M Futures)のポジションモードを **one-way** に設定
- APIキーは「先物取引」権限のみ(出金権限なし)で発行

### 3. ラッコサーバーにSSH接続

cPanelの「SSHアクセス」からSSHキーを設定し、接続します(詳細はラッコサーバーの
公式マニュアルを参照)。

```bash
ssh -p <ポート番号> <ユーザー名>@<ホスト名>
```

### 4. GitHubからclone

```bash
cd ~
git clone https://github.com/<あなたのユーザー名>/<リポジトリ名>.git bitget_arb_bot
cd bitget_arb_bot
```

### 5. Python仮想環境の作成と依存パッケージのインストール

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

`python3`コマンドが見つからない場合や特定バージョンを使いたい場合は、
cPanelの「Setup Python App」機能で作成したPython実行環境のパスを
代わりに使ってください(ラッコサーバーの公式マニュアルに手順があります)。

### 6. `.env` を作成

```bash
cp .env.example .env
nano .env   # または vi .env
```

まずは `DRY_RUN=true` のまま保存してください。

### 7. 手動で1回テスト実行

```bash
source venv/bin/activate
python run_once.py
cat bot.log
cat trades.csv
deactivate
```

エラーなく動き、`bot.log`にログが出力されていればOKです。

### 8. cPanelでcronジョブを設定

cPanel > 「Cronジョブ」を開き、以下を設定します。

- 分: `*/1`(1分ごと) 、時・日・月・曜日: `*`
- コマンド:

```bash
/home/<ユーザー名>/bitget_arb_bot/venv/bin/python /home/<ユーザー名>/bitget_arb_bot/run_once.py >> /home/<ユーザー名>/bitget_arb_bot/cron_stdout.log 2>&1
```

`<ユーザー名>`はラッコサーバーのアカウントのホームディレクトリ名に置き換えてください
(`echo ~`で確認できます)。

### 9. 動作確認

数分待ってから、以下でログが増えているか確認します。

```bash
tail -f ~/bitget_arb_bot/bot.log
```

`Ctrl+C`で監視を終了できます。`state.json`の中身も確認しておくと状態遷移が
わかりやすいです。

```bash
cat ~/bitget_arb_bot/state.json
```

## 実運用への切り替え

1. `trades.csv` / `bot.log` を見て、想定通りの判断・タイミングでエントリー/決済が
   発生しているか、DRY_RUNのまま数日〜数週間確認する
2. `TRADE_NOTIONAL_USDT`(1脚あたりの名目額)を必ず少額に設定する
3. `LEVERAGE` は低め(2〜3倍)のまま維持する
4. `.env` の `BITGET_API_KEY` / `BITGET_API_SECRET` / `BITGET_PASSPHRASE` を設定
5. `.env` の `DRY_RUN=true` を `DRY_RUN=false` に変更
6. 次回のcron実行時に、初回のみ自動で `set-margin-mode` / `set-leverage` が
   両銘柄に対して実行されます(`state.json`の`account_configured`で一度きり
   実行されたことを管理しています)
7. しばらくはこまめに `bot.log` / `trades.csv` を確認する

## コードを更新する場合

手元で変更 → GitHubにpush → サーバー側で `git pull` という流れになります。

```bash
# 手元
git add .
git commit -m "変更内容"
git push

# サーバー側(SSH)
cd ~/bitget_arb_bot
git pull
source venv/bin/activate
pip install -r requirements.txt   # 依存関係が変わった場合のみ
deactivate
```

`.env` や `state.json` は `.gitignore` で除外されているため、`git pull`しても
上書きされません。

## 多重起動防止(ロック)について

cronの実行間隔(1分)より処理が長引いた場合、前回の実行がまだ終わっていない
状態で次のcronが起動することがあります。`run_once.py`は`bot.lock`ファイルを
使った排他ロックを行っており、前回の実行が終わっていない場合は何もせず
即座に終了します(二重発注防止)。ログに
「前回の実行がまだ終わっていないため、今回はスキップします。」と出た場合は
この状態です。頻発する場合はネットワーク遅延やAPI応答遅延を疑ってください。

## 制限事項・既知の未対応事項

- cronは1分間隔が現実的な下限です(共用サーバーの負荷制限のため、それ以上
  高頻度にはしないことを推奨します)。より高頻度な監視が必要な場合は、
  Oracle Cloud Free Tier等の常時稼働可能な環境への移行を検討してください。
- 成行注文(market order)を使用しているため、板が薄い局面ではスリッページが
  想定より大きくなる可能性があります。
- 同時に保有できるペアポジションは1組のみです。
- ファンディングレートは戦略判断に織り込んでいません。
- 注文の最小数量・価格の呼値(tick size)・サイズの丸め処理は簡略化しています
  (`SIZE_DECIMALS_A`/`SIZE_DECIMALS_B`)。実運用前に `BitgetClient.get_contract_config()`
  で `XAUUSDT` / `XAUTUSDT` の最小注文数量・数量精度を確認し、必要に応じて調整してください。
- ポジション保有中にサーバー側で長期間cronが失敗し続けた場合(例: サーバー障害)、
  Bitget側のポジションはボットの管理外で存在し続けます。異常が疑われる場合は
  必ずBitgetのUIで直接ポジション状況を確認してください。
