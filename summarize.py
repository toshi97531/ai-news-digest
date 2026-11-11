#!/usr/bin/env python3
"""
summarize.py - ai_news.py が出力した当日のJSONを読み、「今日のまとめ」(AI動向 + ジャンル別) ページを生成する。

生成には Claude を使う (環境変数 ANTHROPIC_API_KEY と anthropic SDK が必要: pip install anthropic)。
キーが無い / 失敗した場合は、見出し一覧だけのページを出力して終了する (exit 0)。

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
from pathlib import Path

CLAUDE_MODEL = os.environ.get("SUMMARY_MODEL") or "claude-opus-5-5"
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


GENRE_SYSTEM = """あなたは幅広い分野を担当するニュース編集者です。
与えられた記事リスト（直近24時間のニュース。ジャンルごとに分かれている）をもとに、日本語でジャンル別のまとめを書いてください。

書き方:
- 読者は子ども向け教室を運営する人。仕事関連のジャンルは「現場で役立つ視点」、それ以外のジャンルは「気軽に楽しめる話題」として紹介する。
- 記事リストに書かれていることだけを根拠にし、推測や記事にない事実を加えない。
- 記事に言及するときは、記事リストの [n] 番号を文末に付ける（例: 「...が発表された [12]」）。
- Markdownで出力する。見出しは「## 」、箇条書きは「- 」。表は使わない。

構成:
記事リストに出てくるジャンルごとに、記事リストと同じ順番で、
## ジャンル名
- 主な話題を2〜4行の箇条書きで（同じ話題の記事は1行にまとめる）。
ひとこと: そのジャンルの今日の空気感や、仕事関連なら活かし方を1文で。
記事がジャンルとあまり関係ない場合は、無理にこじつけず関係のある記事だけを使う。"""


def build_prompt(items: list[dict], date_str: str, start: int = 1, by_genre: bool = False) -> str:
    lines = [f"日付: {date_str}", f"記事数: {len(items)}", "", "記事リスト:"]
    cur = None
    for i, it in enumerate(items, start):
        if by_genre and it.get("genre") != cur:
            cur = it.get("genre")
            lines.append(f"\n■ ジャンル: {cur}" + (" (日替わり・息抜き)" if it.get("rotate") else " (仕事関連)"))
        title = it.get("title_ja") or it["title"]
        summ = (it.get("summary_ja") or it.get("summary") or "")[:200]
        lines.append(f"[{i}] {title} ({it['source']})")
        if summ:
            lines.append(f"    {summ}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 生成エンジン
# ---------------------------------------------------------------------------
def summarize_with_claude(prompt: str, system: str = SYSTEM) -> tuple[str, str] | None:
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
            system=system,
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
<title>今日のまとめ {date}</title>
<style>
:root{{--bg:#fafafa;--fg:#1a1a1a;--muted:#6b6b6b;--card:#fff;--line:#e4e4e4;--acc:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--muted:#9a9a9a;--card:#1b1b1b;--line:#2c2c2c;--acc:#7aa2ff}}}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;background:var(--bg);color:var(--fg);line-height:1.75}}
header{{padding:20px 24px;border-bottom:1px solid var(--line)}}
h1{{margin:0;font-size:20px}} .meta{{color:var(--muted);font-size:13px;margin-top:4px}}
nav a{{color:var(--acc);text-decoration:none;margin-right:14px;font-size:14px}}
main{{max-width:760px;margin:0 auto;padding:8px 24px 40px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 24px 16px;margin-top:16px}}
h2{{font-size:17px;margin:28px 0 0}}
h3{{font-size:18px;border-left:4px solid var(--acc);padding-left:10px;margin:28px 0 8px}}
h4{{font-size:16px;margin:20px 0 6px}}
ul{{padding-left:22px}} li{{margin:4px 0}}
a.ref{{color:var(--acc);text-decoration:none;font-size:12px;vertical-align:super;margin-left:2px}}
.refs{{font-size:13px;color:var(--muted);margin-top:24px;border-top:1px solid var(--line);padding-top:12px}}
.refs ol{{padding-left:22px}} .refs a{{color:var(--acc);text-decoration:none}}
.archive{{font-size:13px;color:var(--muted);margin-top:20px}} .archive a{{color:var(--acc);text-decoration:none;margin-right:10px}}
footer{{color:var(--muted);font-size:12px;padding:16px 24px;text-align:center}}
</style></head><body>
<header><h1>今日のまとめ</h1>
<div class="meta">{date} / 記事{n}本をもとに生成 / 生成: {engine}</div>
<nav style="margin-top:8px"><a href="index.html">記事一覧へ</a><a href="summary.html">最新のまとめ</a><a href="#ai">AI動向</a><a href="#genres">ジャンル別</a></nav>
</header>
<main>
<h2 id="ai">AI動向まとめ</h2>
<article>{body}</article>
<h2 id="genres">ジャンル別まとめ</h2>
<article>{genre_body}</article>
<div class="refs"><strong>参照記事</strong><ol>{refs}</ol></div>
<div class="archive"><strong>過去のまとめ:</strong> {archive}</div>
</main>
<footer>要約はAIが自動生成したものです。重要な判断の前に元記事をご確認ください。</footer>
</body></html>"""


def render_page(md: str | None, items: list[dict], date_str: str, engine: str, archive: list[str],
                genre_md: str | None = None) -> str:
    fail = "<p>まとめは生成できませんでした（Claude APIキーが未設定、または生成に失敗）。下の参照記事をご覧ください。</p>"
    body = md_to_html(md, items) if md else fail
    genre_body = md_to_html(genre_md, items) if genre_md else fail
    refs = "".join(
        f'<li><a href="{html.escape(it["link"])}" target="_blank" rel="noopener">{html.escape(it.get("title_ja") or it["title"])}</a>'
        f' <span>({html.escape(it["source"])})</span></li>' for it in items)
    arch = " ".join(f'<a href="summary-{d}.html">{d}</a>' for d in archive) or "なし"
    return PAGE.format(date=date_str, n=len(items), engine=html.escape(engine), body=body, genre_body=genre_body,
                       refs=refs, archive=arch)


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
    all_items = json.loads(src.read_text(encoding="utf-8"))
    ai_items = [i for i in all_items if i.get("genre", "AI") == "AI"][:MAX_ITEMS]
    # ジャンル別: 仕事関連 -> 日替わり の順、ジャンル内は元の並び (新しい順)
    order = list(dict.fromkeys(i["genre"] for i in all_items if i.get("genre", "AI") != "AI"
                               and not i.get("rotate")))
    order += list(dict.fromkeys(i["genre"] for i in all_items if i.get("rotate")))
    genre_items = sorted((i for i in all_items if i.get("genre", "AI") != "AI"), key=lambda i: order.index(i["genre"]))
    items = ai_items + genre_items  # [n] 番号は通し番号
    prompt = build_prompt(ai_items, date_str)

    md, engine = None, "なし"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  ! ANTHROPIC_API_KEY が未設定のため、まとめは生成しません", file=sys.stderr)
    res = summarize_with_claude(prompt)
    if res:
        md, engine = res
    genre_md = None
    if genre_items:
        res = summarize_with_claude(build_prompt(genre_items, date_str, start=len(ai_items) + 1, by_genre=True),
                                    system=GENRE_SYSTEM)
        if res:
            genre_md = res[0]
            engine = res[1] if engine == "なし" else engine
            print("  ✓ ジャンル別まとめ生成", file=sys.stderr)
    if md or genre_md:
        (out / f"summary-{date_str}.md").write_text(
            "# AI動向まとめ\n\n" + (md or "(生成失敗)") + "\n\n# ジャンル別まとめ\n\n" + (genre_md or "(生成失敗)"),
            encoding="utf-8")
        print(f"  ✓ まとめ生成: {engine}", file=sys.stderr)
    else:
        print("  ! まとめを生成できませんでした (見出し一覧のみ出力)", file=sys.stderr)

    archive = sorted({re.sub(r"^summary-(.*)\.html$", r"\1", f.name) for f in out.glob("summary-????-??-??.html")} | {date_str},
                     reverse=True)[:30]
    page = render_page(md, items, date_str, engine, archive, genre_md)
    (out / f"summary-{date_str}.html").write_text(page, encoding="utf-8")
    (out / "summary.html").write_text(page, encoding="utf-8")
    print(f"出力: {(out / 'summary.html').resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
