#!/usr/bin/env python3
"""
ai_news.py - AI関連ニュースを複数のRSS/Atomフィードから収集し、
Markdown / HTML / JSON のダイジェストを生成する。外部ライブラリ不要。

使い方:
    python3 ai_news.py                 # 直近24時間分を docs/ 配下に出力
    python3 ai_news.py --hours 72      # 直近72時間
    python3 ai_news.py --keywords "LLM,Claude,GPT"   # キーワードを上書き
    python3 ai_news.py --feeds feeds.json            # フィード定義を差し替え
    python3 ai_news.py --open         # 生成したHTMLをブラウザで開く
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# デフォルト設定
# ---------------------------------------------------------------------------
DEFAULT_FEEDS = [
    # 日本語
    {"name": "ITmedia AI+", "url": "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", "lang": "ja", "filter": False},
    {"name": "Googleニュース (AI)", "url": "https://news.google.com/rss/search?q=AI+OR+%E7%94%9F%E6%88%90AI+OR+%E4%BA%BA%E5%B7%A5%E7%9F%A5%E8%83%BD&hl=ja&gl=JP&ceid=JP:ja", "lang": "ja", "filter": False},
    {"name": "Publickey", "url": "https://www.publickey1.jp/atom.xml", "lang": "ja", "filter": True},
    {"name": "Zenn (AI)", "url": "https://zenn.dev/topics/ai/feed", "lang": "ja", "filter": False},
    {"name": "Qiita (LLM)", "url": "https://qiita.com/tags/llm/feed", "lang": "ja", "filter": False},
    # 英語
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en", "filter": False},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en", "filter": False},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/", "lang": "en", "filter": True},
    {"name": "Hacker News (AI検索)", "url": "https://hnrss.org/newest?q=AI+OR+LLM&points=50", "lang": "en", "filter": False},
    {"name": "OpenAI Blog", "url": "https://openai.com/news/rss.xml", "lang": "en", "filter": False},
    {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/rss/", "lang": "en", "filter": False},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "lang": "en", "filter": False},
]

# "filter": True のフィードは、このキーワードに1つ以上一致した記事だけを残す
DEFAULT_KEYWORDS = [
    "AI", "人工知能", "生成AI", "LLM", "大規模言語モデル", "機械学習", "深層学習",
    "ChatGPT", "GPT", "OpenAI", "Claude", "Anthropic", "Gemini", "Copilot",
    "Llama", "Mistral", "DeepSeek", "Hugging Face", "エージェント", "agent",
    "RAG", "Transformer", "NVIDIA", "GPU", "diffusion", "Stable Diffusion", "Sora",
]

USER_AGENT = "Mozilla/5.0 (compatible; ai-news-digest/1.0)"
NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "content": "http://purl.org/rss/1.0/modules/content/", "rss1": "http://purl.org/rss/1.0/",
      "media": "http://search.yahoo.com/mrss/"}


# ---------------------------------------------------------------------------
# 取得・パース
# ---------------------------------------------------------------------------
def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_tags(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
OG_RE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']'
                   r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.IGNORECASE)


def feed_image(el, *raw_html: str | None) -> str:
    """フィード項目から画像URLを探す (media:*, enclosure, 本文内の img)。"""
    for tag in ("media:thumbnail", "media:content"):
        for m in el.findall(tag, NS):
            url = m.get("url", "")
            if url and (m.get("medium") in (None, "image")) and not m.get("type", "image").startswith("video"):
                return url
    for m in el.findall("media:group/media:thumbnail", NS) + el.findall("media:group/media:content", NS):
        if m.get("url"):
            return m.get("url")
    for enc in el.findall("enclosure"):
        if enc.get("type", "").startswith("image") and enc.get("url"):
            return enc.get("url")
    for h in raw_html:
        if h:
            m = IMG_RE.search(h)
            if m and not m.group(1).startswith("data:"):
                return html.unescape(m.group(1))
    return ""


def page_image(url: str) -> str | None:
    """記事ページの og:image を取得する。失敗時は None。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if "html" not in r.headers.get("Content-Type", "html"):
                return ""
            head = r.read(120_000).decode("utf-8", "ignore")
        m = OG_RE.search(head)
        if not m:
            return ""
        img = html.unescape(m.group(1) or m.group(2) or "")
        return urllib.parse.urljoin(url, img) if img else ""
    except Exception:
        return None


def fill_images(items: list[dict], cache_path: Path, limit: int = 120) -> int:
    """画像のない記事について記事ページから og:image を取得する。"""
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except Exception:
        cache = {}
    todo = []
    for it in items:
        if it.get("image"):
            continue
        if it["link"] in cache:
            it["image"] = cache[it["link"]]
        elif len(todo) < limit:
            todo.append(it)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for it, img in zip(todo, ex.map(lambda i: page_image(i["link"]), todo)):
            if img is None:
                continue
            it["image"] = img
            cache[it["link"]] = img
            done += 1
    try:
        if len(cache) > 3000:
            cache = dict(list(cache.items())[-2000:])
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return done


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:  # RFC 822 (RSS 2.0)
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        pass
    try:  # ISO 8601 (Atom / RSS1.0 dc:date)
        s2 = s.replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s2)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


def _text(el, *paths) -> str | None:
    for p in paths:
        found = el.find(p, NS)
        if found is not None and (found.text or "").strip():
            return found.text
    return None


def parse_feed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    tag = root.tag.lower()
    items: list[dict] = []

    if tag.endswith("feed"):  # Atom
        for e in root.findall("atom:entry", NS):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href", "")
                    break
            items.append({
                "title": strip_tags(_text(e, "atom:title")),
                "link": link.strip(),
                "summary": strip_tags(_text(e, "atom:summary", "atom:content")),
                "date": parse_date(_text(e, "atom:published", "atom:updated")),
                "image": feed_image(e, _text(e, "atom:content"), _text(e, "atom:summary")),
            })
    elif tag.endswith("rdf"):  # RSS 1.0
        for e in root.findall("rss1:item", NS):
            items.append({
                "title": strip_tags(_text(e, "rss1:title")),
                "link": (_text(e, "rss1:link") or "").strip(),
                "summary": strip_tags(_text(e, "rss1:description", "content:encoded")),
                "date": parse_date(_text(e, "dc:date")),
                "image": feed_image(e, _text(e, "content:encoded"), _text(e, "rss1:description")),
            })
    else:  # RSS 2.0
        for e in root.iter("item"):
            items.append({
                "title": strip_tags(_text(e, "title")),
                "link": (_text(e, "link") or "").strip(),
                "summary": strip_tags(_text(e, "description", "content:encoded")),
                "date": parse_date(_text(e, "pubDate", "dc:date")),
                "image": feed_image(e, _text(e, "content:encoded"), _text(e, "description")),
            })
    return [i for i in items if i["title"] and i["link"]]


def collect_one(feed: dict, since: dt.datetime, keywords: list[str]) -> tuple[dict, list[dict], str | None]:
    try:
        raw = fetch(feed["url"])
        entries = parse_feed(raw)
    except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as e:
        return feed, [], f"{type(e).__name__}: {e}"

    kw_re = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE) if keywords else None
    out = []
    for it in entries:
        if it["date"] and it["date"] < since:
            continue
        if feed.get("filter") and kw_re and not kw_re.search(it["title"] + " " + it["summary"]):
            continue
        it["source"] = feed["name"]
        it["lang"] = feed.get("lang", "")
        it["genre"] = feed.get("genre", "AI")
        it["summary"] = it["summary"][:240]
        out.append(it)
    return feed, out, None


def dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for it in items:
        key = re.sub(r"[?#].*$", "", it["link"]).rstrip("/").lower()
        tkey = re.sub(r"\W+", "", it["title"].lower())[:60]
        if key in seen or tkey in seen:
            continue
        seen.add(key)
        seen.add(tkey)
        result.append(it)
    return result


# ---------------------------------------------------------------------------
# 翻訳 (英語 -> 日本語)。APIキー不要のGoogle翻訳エンドポイントを利用し、結果をキャッシュする
# ---------------------------------------------------------------------------
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ja&dt=t&q="


def translate_text(text: str) -> str | None:
    if not text.strip():
        return ""
    try:
        raw = fetch(TRANSLATE_URL + urllib.parse.quote(text), timeout=15)
        data = json.loads(raw.decode("utf-8"))
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
    except Exception:
        return None


def translate_items(items: list[dict], cache_path: Path) -> int:
    """lang が ja 以外の記事に title_ja / summary_ja を付ける。翻訳できた件数を返す。"""
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except Exception:
        cache = {}
    todo: list[tuple[dict, str, str]] = []
    for it in items:
        if it.get("lang") == "ja":
            continue
        for field in ("title", "summary"):
            src = it.get(field, "")
            if not src:
                continue
            if src in cache:
                it[field + "_ja"] = cache[src]
            else:
                todo.append((it, field, src))

    done = 0
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for (it, field, src), ja in zip(todo, ex.map(lambda t: translate_text(t[2]), todo)):
            if ja:
                it[field + "_ja"] = ja
                cache[src] = ja
                done += 1
    try:
        # キャッシュが肥大化しないよう直近の分だけ保持
        if len(cache) > 3000:
            cache = dict(list(cache.items())[-2000:])
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return done


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def fmt_date(d: dt.datetime | None, tz: dt.tzinfo) -> str:
    return d.astimezone(tz).strftime("%m/%d %H:%M") if d else "--/-- --:--"


def render_markdown(items: list[dict], errors: list[tuple[str, str]], now: dt.datetime, hours: int, tz,
                    genres: list[str] | None = None) -> str:
    lines = [f"# AIニュースダイジェスト {now.astimezone(tz):%Y-%m-%d %H:%M}",
             "", f"直近{hours}時間 / {len(items)}件", ""]
    order = {g: i for i, g in enumerate(genres or [])}
    by_src: dict[tuple[str, str], list[dict]] = {}
    for it in sorted(items, key=lambda i: order.get(i.get("genre", "AI"), len(order))):
        by_src.setdefault((it.get("genre", "AI"), it["source"]), []).append(it)
    cur_genre = None
    for (genre, src), its in by_src.items():
        if genre != cur_genre:
            lines.append(f"# {genre}")
            lines.append("")
            cur_genre = genre
        lines.append(f"## {src} ({len(its)})")
        for it in its:
            title = it.get("title_ja") or it["title"]
            lines.append(f"- [{title}]({it['link']}) `{fmt_date(it['date'], tz)}`")
            if it.get("title_ja"):
                lines.append(f"  - 原題: {it['title']}")
            summary = it.get("summary_ja") or it["summary"]
            if summary:
                lines.append(f"  - {summary}")
        lines.append("")
    if errors:
        lines.append("## 取得失敗")
        lines += [f"- {n}: {e}" for n, e in errors]
    return "\n".join(lines)


HTML_TMPL = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIニュースダイジェスト {date}</title>
<style>
:root{{--bg:#fafafa;--fg:#1a1a1a;--muted:#6b6b6b;--card:#fff;--line:#e4e4e4;--acc:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--muted:#9a9a9a;--card:#1b1b1b;--line:#2c2c2c;--acc:#7aa2ff}}}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;background:var(--bg);color:var(--fg);line-height:1.55}}
header{{padding:20px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:1}}
h1{{margin:0;font-size:20px}} .meta{{color:var(--muted);font-size:13px;margin-top:4px}}
.tools{{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}}
input,select{{padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font-size:14px}}
main{{max-width:960px;margin:0 auto;padding:16px 24px}}
.item{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0;display:flex;gap:14px;align-items:flex-start}}
.item .body{{flex:1;min-width:0}}
.thumb{{flex:0 0 120px;width:120px;height:80px;border-radius:6px;object-fit:cover;background:var(--line)}}
@media(max-width:600px){{.thumb{{flex-basis:88px;width:88px;height:64px}}}}
.item a{{color:var(--acc);text-decoration:none;font-weight:600;font-size:15px}} .item a:hover{{text-decoration:underline}}
.src{{font-size:12px;color:var(--muted);margin-top:4px}} .sum{{font-size:13px;color:var(--muted);margin-top:6px}}
.orig{{font-size:12px;color:var(--muted);margin-top:2px}} .hidden{{display:none}} footer{{color:var(--muted);font-size:12px;padding:16px 24px}}
.badge{{display:inline-block;padding:1px 6px;border-radius:4px;background:var(--line);margin-right:6px}}
.badge.genre{{background:#fde68a;color:#78350f}}
.tabs{{margin-top:10px;display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}}
.tab{{flex:0 0 auto;padding:5px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--fg);font-size:13px;cursor:pointer}}
.tab small{{color:var(--muted)}} .tab.on{{background:var(--acc);border-color:var(--acc);color:#fff}} .tab.on small{{color:#fff}}
</style></head><body>
<header><h1>AIニュースダイジェスト</h1>
<div class="meta">{date} 生成 / 直近{hours}時間 / <span id="count">{n}</span>件 · <a href="summary.html" style="color:var(--acc);text-decoration:none;font-weight:600">今日のAI動向まとめを読む →</a></div>
<div class="tools"><input id="q" placeholder="キーワードで絞り込み" oninput="f()">
<select id="src" onchange="f()"><option value="">すべてのソース</option>{src_opts}</select>
<select id="lang" onchange="f()"><option value="">日本語+英語</option><option value="ja">日本語</option><option value="en">英語</option></select></div>
<div class="tabs">{tabs}</div>
</header><main id="list">{items}</main>
<footer>{errors}</footer>
<script>
let G='';function g(b){{G=b.dataset.g;for(const t of document.querySelectorAll('.tab'))t.classList.toggle('on',t===b);f()}}
function f(){{const q=document.getElementById('q').value.toLowerCase(),s=document.getElementById('src').value,l=document.getElementById('lang').value;let n=0;
for(const el of document.querySelectorAll('.item')){{const ok=(!q||el.textContent.toLowerCase().includes(q))&&(!s||el.dataset.src===s)&&(!l||el.dataset.lang===l)&&(!G||el.dataset.genre===G);el.classList.toggle('hidden',!ok);if(ok)n++}}
document.getElementById('count').textContent=n}}
</script></body></html>"""


def render_html(items: list[dict], errors, now, hours, tz, genres: list[str] | None = None) -> str:
    e = html.escape
    counts: dict[str, int] = {}
    for it in items:
        counts[it.get("genre", "AI")] = counts.get(it.get("genre", "AI"), 0) + 1
    tabs = [f'<button class="tab on" data-g="" onclick="g(this)">すべて <small>{len(items)}</small></button>']
    for gname in list(genres or []) + [x for x in counts if x not in (genres or [])]:
        if counts.get(gname):
            tabs.append(f'<button class="tab" data-g="{e(gname)}" onclick="g(this)">{e(gname)} <small>{counts[gname]}</small></button>')
    srcs = sorted({it["source"] for it in items})
    rows = []
    for it in items:
        rows.append(
            f'<div class="item" data-src="{e(it["source"])}" data-lang="{e(it["lang"])}" data-genre="{e(it.get("genre", "AI"))}">'
            + (f'<a href="{e(it["link"])}" target="_blank" rel="noopener"><img class="thumb" src="{e(it["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></a>' if it.get("image") else "")
            + f'<div class="body"><a href="{e(it["link"])}" target="_blank" rel="noopener">{e(it.get("title_ja") or it["title"])}</a>'
            + (f'<div class="orig">{e(it["title"])}</div>' if it.get("title_ja") else "")
            + f'<div class="src">'
            + (f'<span class="badge genre">{e(it["genre"])}</span>' if it.get("genre", "AI") != "AI" else "")
            + f'<span class="badge">{e(it["lang"]) or "-"}</span>{e(it["source"])} · {fmt_date(it["date"], tz)}</div>'
            + (f'<div class="sum">{e(it.get("summary_ja") or it["summary"])}</div>' if it["summary"] else "") + "</div></div>")
    err_html = ("取得失敗: " + " / ".join(f"{e(n)} ({e(m)})" for n, m in errors)) if errors else ""
    return HTML_TMPL.format(date=f"{now.astimezone(tz):%Y-%m-%d %H:%M}", hours=hours, n=len(items),
                            src_opts="".join(f'<option>{e(s)}</option>' for s in srcs),
                            items="\n".join(rows), errors=err_html, tabs="".join(tabs))


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AIニュースをRSSから収集してダイジェストを生成")
    p.add_argument("--hours", type=int, default=24, help="何時間前までの記事を対象にするか (既定: 24)")
    p.add_argument("--keywords", help="フィルタ用キーワード (カンマ区切り)")
    p.add_argument("--feeds", help="フィード定義JSONのパス")
    p.add_argument("--out", default="docs", help="出力ディレクトリ (既定: docs)")
    p.add_argument("--max-per-source", type=int, default=15, help="ソースごとの最大件数")
    p.add_argument("--open", action="store_true", help="生成後にHTMLをブラウザで開く")
    p.add_argument("--no-translate", action="store_true", help="英語記事の日本語翻訳をしない")
    p.add_argument("--no-images", action="store_true", help="記事ページからのアイキャッチ画像取得をしない")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)

    feeds = DEFAULT_FEEDS
    feeds_path = Path(a.feeds) if a.feeds else Path(__file__).with_name("feeds.json")
    if a.feeds or feeds_path.exists():
        feeds = json.loads(feeds_path.read_text(encoding="utf-8"))
    genres = list(dict.fromkeys(f.get("genre", "AI") for f in feeds))
    keywords = [k.strip() for k in a.keywords.split(",")] if a.keywords else DEFAULT_KEYWORDS

    tz = dt.datetime.now().astimezone().tzinfo
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=a.hours)

    items: list[dict] = []
    errors: list[tuple[str, str]] = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for feed, got, err in ex.map(lambda f: collect_one(f, since, keywords), feeds):
            if err:
                errors.append((feed["name"], err))
                if not a.quiet:
                    print(f"  ! {feed['name']}: {err}", file=sys.stderr)
            else:
                got.sort(key=lambda i: i["date"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
                items += got[: feed.get("max", a.max_per_source)]
                if not a.quiet:
                    print(f"  ✓ {feed['name']}: {len(got)}件", file=sys.stderr)

    items = dedupe(items)
    items.sort(key=lambda i: i["date"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if not a.no_images:
        n = fill_images(items, out / "images.json")
        if not a.quiet:
            print(f"  画像: {n}件 (記事ページから取得)", file=sys.stderr)
    if not a.no_translate:
        n = translate_items(items, out / "translations.json")
        if not a.quiet:
            print(f"  翻訳: {n}件 (新規)", file=sys.stderr)
    stamp = now.astimezone(tz).strftime("%Y-%m-%d")
    (out / f"digest-{stamp}.md").write_text(render_markdown(items, errors, now, a.hours, tz, genres), encoding="utf-8")
    (out / f"digest-{stamp}.json").write_text(
        json.dumps([{**i, "date": i["date"].isoformat() if i["date"] else None} for i in items],
                   ensure_ascii=False, indent=1), encoding="utf-8")
    html_path = out / "index.html"
    html_path.write_text(render_html(items, errors, now, a.hours, tz, genres), encoding="utf-8")
    (out / f"digest-{stamp}.html").write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")

    if not a.quiet:
        print(f"\n{len(items)}件を出力: {html_path.resolve()}", file=sys.stderr)
    if a.open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
