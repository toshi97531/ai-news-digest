#!/usr/bin/env python3
"""
summarize.py - ai_news.py が出力した当日のJSONを読み、「今日のAI動向まとめ」ページを生成する。

生成エンジン (上から順に試す):
  1. Claude (環境変数 ANTHROPIC_API_KEY があるとき。anthropic SDK が必要: pip install anthropic)
  2. GitHub Models (環境変数 GITHUB_TOKEN があるとき。GitHub Actions 内なら追加設定不要・無料)
どちらも使えない / 失敗した場合は、見出し一覧だけのページを出力して終了する (exit 0)。

使い方:
    python3 summarize.py                 # docs/digest-<今日>.json を読んで docs/summary.html を出力
    python3 summarize.py --date 2026-11-05
    SUMMARY_MODEL=claude-haiku-5-5 python3 summarize.py   # Claudeのモデルを変える
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CLAUDE_MODEL = os.environ.get("SUMMARY_MODEL", "claude-opus-5-5")
GITHUB_MODEL = os.environ.get("SUMMARY_FALLBACK_MODEL", "openai/gpt-4.1-mini")
MAX_ITEMS = 100

SYSTEM = """あなたはAI業界を専門とするテクノロジー記者です。
与えられた記事リスト（直近24時間のAI関連ニュース）をもとに、日本語で「今日のAI動向まとめ」を書いてください。

書き方:
- 読者は忙しいビジネスパーソン・エンジニア。5分で全体像がつかめるようにする。
- 記事リストに書かれていることだけを根拠にし、推測や記事にない事実を加えない。
- 同じ話題を扱う記事は1つのトピックにまとめる。
- 記事に言及するときは、記事リストの [n] 番号を文末に付ける（例: 「...を発表した [12]」）。
- Markdownで出力する。見出しは「## 」、箇条書きは「- 」。表は使わない。

構成 (この順番で、見出し名もこの通りに):
## 今日の3行まとめ
- 最重要トピックを3行で。
## 主要トピック
各トピックごとに「### トピック名」の見出しをつけ、何が起きたか・なぜ重要か・今後の見どころを3〜6文で。トピックは3〜6個。
## 分野別の動き
- モデル・研究 / 企業・ビジネス / 日本国内 / 開発者向け・ツール / 社会・規制 のうち該当があるものだけ、各1〜3行。
## 今日のひとこと
全体の流れを1〜2文で。"""


def build_prompt(items: list[dict], date_str: str) -> str:
    lines = [f"日付: {date_str}", f"記事数: {len(items)}", "", "記事リスト:"]
    for i, it in enumerate(items, 1):
        title = it.get("title_ja") or it["title"]
        summ = (it.get("summary_ja") or it.get("summary") or "")[:200]
        lines.append(f"[{i}] {title} ({it['source']})")
        if summ:
            lines.append(f"    {summ}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 生成エンジン
# ---------------------------------------------------------------------------
def summarize_with_claude(prompt: str) -> tuple[str, str] | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        print("  ! anthropic SDK が未インストール (pip install anthropic)", file=sys.stderr)
        return None
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        print(f"  ! Claude API エラー: {e}", file=sys.stderr)
        return None
    if resp.stop_reason == "refusal":
        print("  ! Claude が応答を拒否しました", file=sys.stderr)
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return (text, f"Claude ({resp.model})") if text else None


GITHUB_CANDIDATES = [GITHUB_MODEL, "openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4.1", "openai/gpt-4o",
                     "meta/Llama-4-Scout-17B-16E-Instruct", "mistral-ai/mistral-small-2503"]


def _gh_request(url: str, token: str, body: bytes | None = None) -> tuple[int, str]:
    req = urllib.request.Request(
        url, data=body, method="POST" if body else "GET",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json", "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "ai-news-digest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def summarize_with_github_models(prompt: str) -> tuple[str, str] | None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    # 利用可能なモデル一覧を取得し、候補のうち存在するものを順に試す
    available: list[str] = []
    status, text = _gh_request("https://models.github.ai/catalog/models", token)
    try:
        available = [m.get("id", "") for m in json.loads(text)]
        print(f"  GitHub Models カタログ: {len(available)}件 (例: {', '.join(available[:6])})", file=sys.stderr)
    except Exception:
        print(f"  ! GitHub Models カタログ取得失敗: HTTP {status}: {text[:300]!r}", file=sys.stderr)
    candidates = [m for m in GITHUB_CANDIDATES if not available or m in available]
    if available and not candidates:
        candidates = [m for m in available if "gpt" in m.lower()][:2] or available[:1]
    for model in dict.fromkeys(candidates):
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "max_tokens": 4000,
        }).encode()
        status, text = _gh_request("https://models.github.ai/inference/chat/completions", token, body)
        try:
            data = json.loads(text)
            out = data["choices"][0]["message"]["content"].strip()
            if out:
                return out, f"GitHub Models ({model})"
            print(f"  ! GitHub Models ({model}) 応答が空: {text[:300]!r}", file=sys.stderr)
        except Exception as e:
            print(f"  ! GitHub Models ({model}) エラー: HTTP {status} {e}: {text[:300]!r}", file=sys.stderr)
            if status in (401, 403):
                break
    return None


# ---------------------------------------------------------------------------
# Markdown -> HTML (必要最小限)
# ---------------------------------------------------------------------------
def md_to_html(md: str, items: list[dict]) -> str:
    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)

        def ref(m):
            n = int(m.group(1))
            if 1 <= n <= len(items):
                it = items[n - 1]
                return f'<a class="ref" href="{html.escape(it["link"])}" target="_blank" rel="noopener" title="{html.escape(it.get("title_ja") or it["title"])}">[{n}]</a>'
            return m.group(0)
        return re.sub(r"\[(\d+)\]", ref, s)

    out, in_list = [], False
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            if in_list:
                out.append("</ul>"); in_list = False
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            if in_list:
                out.append("</ul>"); in_list = False
            lvl = min(len(m.group(1)) + 1, 4)  # ## -> h3 (h1/h2はページ側で使う)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^\s*[-*]\s+(.*)", s)
        if m:
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(m.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        out.append(f"<p>{inline(s.strip())}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日のAI動向まとめ {date}</title>
<style>
:root{{--bg:#fafafa;--fg:#1a1a1a;--muted:#6b6b6b;--card:#fff;--line:#e4e4e4;--acc:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--muted:#9a9a9a;--card:#1b1b1b;--line:#2c2c2c;--acc:#7aa2ff}}}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;background:var(--bg);color:var(--fg);line-height:1.75}}
header{{padding:20px 24px;border-bottom:1px solid var(--line)}}
h1{{margin:0;font-size:20px}} .meta{{color:var(--muted);font-size:13px;margin-top:4px}}
nav a{{color:var(--acc);text-decoration:none;margin-right:14px;font-size:14px}}
main{{max-width:760px;margin:0 auto;padding:8px 24px 40px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 24px 16px;margin-top:16px}}
h3{{font-size:18px;border-left:4px solid var(--acc);padding-left:10px;margin:28px 0 8px}}
h4{{font-size:16px;margin:20px 0 6px}}
ul{{padding-left:22px}} li{{margin:4px 0}}
a.ref{{color:var(--acc);text-decoration:none;font-size:12px;vertical-align:super;margin-left:2px}}
.refs{{font-size:13px;color:var(--muted);margin-top:24px;border-top:1px solid var(--line);padding-top:12px}}
.refs ol{{padding-left:22px}} .refs a{{color:var(--acc);text-decoration:none}}
.archive{{font-size:13px;color:var(--muted);margin-top:20px}} .archive a{{color:var(--acc);text-decoration:none;margin-right:10px}}
footer{{color:var(--muted);font-size:12px;padding:16px 24px;text-align:center}}
</style></head><body>
<header><h1>今日のAI動向まとめ</h1>
<div class="meta">{date} / 記事{n}本をもとに生成 / 生成: {engine}</div>
<nav style="margin-top:8px"><a href="index.html">記事一覧へ</a><a href="summary.html">最新のまとめ</a></nav>
</header>
<main>
<article>{body}</article>
<div class="refs"><strong>参照記事</strong><ol>{refs}</ol></div>
<div class="archive"><strong>過去のまとめ:</strong> {archive}</div>
</main>
<footer>要約はAIが自動生成したものです。重要な判断の前に元記事をご確認ください。</footer>
</body></html>"""


def render_page(md: str | None, items: list[dict], date_str: str, engine: str, archive: list[str]) -> str:
    if md:
        body = md_to_html(md, items)
    else:
        body = "<p>本日のまとめは生成できませんでした（要約エンジンが利用できません）。以下の記事一覧をご覧ください。</p>"
    refs = "".join(
        f'<li><a href="{html.escape(it["link"])}" target="_blank" rel="noopener">{html.escape(it.get("title_ja") or it["title"])}</a>'
        f' <span>({html.escape(it["source"])})</span></li>' for it in items)
    arch = " ".join(f'<a href="summary-{d}.html">{d}</a>' for d in archive) or "なし"
    return PAGE.format(date=date_str, n=len(items), engine=html.escape(engine), body=body, refs=refs, archive=arch)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="当日のAIニュースから動向まとめページを生成")
    p.add_argument("--out", default="docs")
    p.add_argument("--date", help="対象日 YYYY-MM-DD (既定: 今日)")
    a = p.parse_args(argv)

    out = Path(a.out)
    date_str = a.date or dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    src = out / f"digest-{date_str}.json"
    if not src.exists():
        print(f"入力が見つかりません: {src}", file=sys.stderr)
        return 1
    items = json.loads(src.read_text(encoding="utf-8"))[:MAX_ITEMS]
    prompt = build_prompt(items, date_str)

    md, engine = None, "なし"
    for fn in (summarize_with_claude, summarize_with_github_models):
        res = fn(prompt)
        if res:
            md, engine = res
            break
    if md:
        (out / f"summary-{date_str}.md").write_text(md, encoding="utf-8")
        print(f"  ✓ まとめ生成: {engine}", file=sys.stderr)
    else:
        print("  ! まとめを生成できませんでした (見出し一覧のみ出力)", file=sys.stderr)

    archive = sorted({re.sub(r"^summary-(.*)\.html$", r"\1", f.name) for f in out.glob("summary-????-??-??.html")} | {date_str},
                     reverse=True)[:30]
    page = render_page(md, items, date_str, engine, archive)
    (out / f"summary-{date_str}.html").write_text(page, encoding="utf-8")
    (out / "summary.html").write_text(page, encoding="utf-8")
    print(f"出力: {(out / 'summary.html').resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
