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
```

出力先 (`--out` で変更可):

| ファイル | 内容 |
|---|---|
| `docs/index.html` | 最新のダイジェスト。キーワード・ソース・言語で絞り込みできる |
| `docs/digest-YYYY-MM-DD.html` | 日付つきの保存版 |
| `docs/digest-YYYY-MM-DD.md` | Markdown版 (Obsidian / Notion などに貼る用) |
| `docs/digest-YYYY-MM-DD.json` | 他ツール連携用の生データ |

## フィードの追加・変更

`feeds.json` を編集してください。各項目:

- `name`: 表示名
- `url`: RSS 2.0 / RSS 1.0 / Atom のURL
- `lang`: `ja` または `en` (絞り込み用)
- `filter`: `true` にすると、AI関連キーワードに一致した記事だけ残す (総合ニュース系に使う)

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
2. 指定時間より古い記事と、`filter: true` のソースでキーワードに当たらない記事を除外
3. URL・タイトルで重複を除去し、新しい順に並べて出力

取得に失敗したフィードはエラーとして末尾に表示され、他のフィードの処理は続行します。
