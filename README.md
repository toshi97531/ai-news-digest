# AI News Digest

複数のニュースサイト・ブログのRSS/Atomフィードから **AI関連ニュースだけ** を集めて、
絞り込み検索つきのHTML・Markdown・JSONのダイジェストを作るツールです。
**Python 3.9以上だけで動き、追加ライブラリは不要**です。

## 使い方

```bash
python3 ai_news.py                # 直近24時間分を docs/ に出力
python3 ai_news.py --open         # 出力後にブラウザで開く
python3 ai_news.py --hours 72     # 直近72時間分
python3 ai_news.py --keywords "Claude,GPT,Gemini"   # フィルタ語を変更
python3 ai_news.py --feeds feeds.json               # フィード一覧を差し替え
python3 ai_news.py --no-translate                   # 英語記事を翻訳しない
python3 ai_news.py --no-images                      # 記事ページからの画像取得をしない
```

出力先 (`--out` で変更可):

| ファイル | 内容 |
|---|---|
| `docs/index.html` | 最新のダイジェスト。キーワード・ソース・言語で絞り込みできる |
| `docs/summary.html` | AIが書いた「今日のまとめ」(AI動向 + ジャンル別。summarize.py が生成) |
| `docs/summary-YYYY-MM-DD.html` | まとめの日付つき保存版 |
| `docs/digest-YYYY-MM-DD.html` | 日付つきの保存版 |
| `docs/digest-YYYY-MM-DD.md` | Markdown版 (Obsidian / Notion などに貼る用) |
| `docs/digest-YYYY-MM-DD.json` | 他ツール連携用の生データ |
| `docs/translations.json` | 英語記事の翻訳キャッシュ (自動生成) |
| `docs/images.json` | アイキャッチ画像URLのキャッシュ (自動生成) |

## 今日のAI動向まとめ (summarize.py)

収集した記事をもとに、Claude が日本語のまとめを書きます。

- **AI動向まとめ**: 3行まとめ・主要トピック・分野別の動き
- **ジャンル別まとめ**: 仕事関連と日替わりジャンルそれぞれの主な話題とひとこと (Claude を1回多く呼ぶので、費用は少し増えます)

```bash
pip install anthropic                       # Claude を使う場合のみ
ANTHROPIC_API_KEY=sk-ant-... python3 summarize.py
```

生成には Claude API を使います。モデルは既定で `claude-opus-5-5`、環境変数 `SUMMARY_MODEL` で変更できます (例: `claude-haiku-5-5` にするとさらに安価)。
キーが無い場合は見出し一覧だけのページを出します。

GitHub Actions で動かすには:

1. https://platform.claude.com/ でAPIキーを作成
2. リポジトリの Settings → Secrets and variables → Actions → **New repository secret** で、Name を `ANTHROPIC_API_KEY` にして登録
3. (任意) 同じ画面の **Variables** タブで `SUMMARY_MODEL` を登録するとモデルを変更できる

費用の目安: 1日1回、記事約90本を入力して日本語のまとめを出力する場合、`claude-opus-5-5` で1回あたり約0.1ドル前後、`claude-haiku-5-5` なら約0.005ドル前後です。

## フィードの追加・変更

`feeds.json` を編集してください。各項目:

- `name`: 表示名
- `url`: RSS 2.0 / RSS 1.0 / Atom のURL
- `lang`: `ja` または `en` (絞り込み用)
- `filter`: `true` にすると、AI関連キーワードに一致した記事だけ残す (総合ニュース系に使う)
- `keywords`: `filter: true` のときの絞り込み語をこのフィードだけ差し替える (例: 集客系の記事から教室・店舗向けのものだけ残す)
- `genre`: ジャンル名 (省略時は `AI`)。ページ上部のジャンルタブになる。`AI` 以外は「今日のAI動向まとめ」には含めない
- `max`: このソースから載せる最大件数 (省略時は `--max-per-source` の15件)

- `rotate`: `true` にすると「日替わりジャンル」の候補になる。候補の中から毎日3つ (`--rotate-count` で変更可) が選ばれ、全候補を一巡するまで同じジャンルは出ない

AI以外のジャンルは、Googleニュースの検索フィードから各5件ずつ載せています (教室経営・集客だけは note のハッシュタグ #教室運営 #習い事教室 #集客 と、中小企業向けの「ミラサポplus」から)。

- 毎日載る仕事関連: 教育・習い事 / 子どもスポーツ / 教室経営・集客
- 日替わり (21候補から毎日3つ): 宇宙・サイエンス / グルメ / 旅行 / 動物 / 映画・ドラマ / 音楽 / ゲーム / 漫画・アニメ / 野球 / サッカー / 歴史 / 健康 / 天気・自然 / 鉄道 / 本 / アート / ファッション / 車 / 将棋・囲碁 / 世界の話題 / 暮らし

ジャンルを変えたいときは、該当行の `name`・`genre` と、URLの `q=` 以降の検索語を書き換えてください。
日替わり候補を増やすときは、行をコピーして `"rotate": true` のまま追加します。

`feeds.json` はスクリプトと同じフォルダにあれば自動で読み込まれます。

## 毎日自動で動かす

### GitHub Actions + GitHub Pages (無料・サーバー不要)

1. このフォルダをGitHubリポジトリにpush
2. リポジトリ設定 → Pages → Source を **GitHub Actions** にする
3. `.github/workflows/digest.yml` が毎朝7時(JST)に実行され、`https://<ユーザー名>.github.io/<リポ名>/` で閲覧できる

### 手元のPCで (cron / タスクスケジューラ)

```bash
# macOS / Linux: 毎朝7時に実行
0 7 * * * cd /path/to/ai-news-digest && python3 ai_news.py --quiet
```

## 仕組み

1. 各フィードを並列で取得 (標準ライブラリ `urllib` + `xml.etree`)
2. アイキャッチ画像をフィード (media:thumbnail, enclosure, 本文内img) から取得し、無ければ記事ページの og:image を取得
3. 英語記事のタイトルと要約を日本語に翻訳 (Google翻訳の無料エンドポイント、結果はキャッシュ)
4. 指定時間より古い記事と、`filter: true` のソースでキーワードに当たらない記事を除外
5. URL・タイトルで重複を除去し、新しい順に並べて出力

取得に失敗したフィードはエラーとして末尾に表示され、他のフィードの処理は続行します。
