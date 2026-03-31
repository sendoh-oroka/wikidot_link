# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import csv
import json
import os
import re
import sys
from collections import defaultdict

FTML_DIR = os.path.join(os.path.dirname(__file__), "ftml")
FILE_LIST = os.path.join(os.path.dirname(__file__), "file-list.csv")
OUTPUT = os.path.join(os.path.dirname(__file__), "data.json")

DOMAIN_PATTERN = re.compile(
    r"https?://(?:scp-jp\.wikidot\.com|ja\.scp-wiki\.net)/"
)

# マジックURIサフィックス除去
MAGIC_URI_PATTERN = re.compile(
    r"/(title|noredirect|tags|parentPage|norender)/.*$"
)

# パターンA: [[[...]]] 形式（URLを含む [[[*http://...]]] も対象）
PATTERN_A = re.compile(r"\[\[\[(\*?[^\]|#\n]+?)(?:\|[^\]]*?)?\]\]\]")

# パターンB: [http://... text] または [*http://... text]（対象ドメインのみ）
PATTERN_B = re.compile(
    r"\[\*?https?://(?:scp-jp\.wikidot\.com|ja\.scp-wiki\.net)/([^\s\]\|]+)[^\]]*\]"
)

# パターンC: 裸URL（テキスト内に直接書かれた対象ドメインのURL）
PATTERN_C = re.compile(
    r"(?<!\[)\*?https?://(?:scp-jp\.wikidot\.com|ja\.scp-wiki\.net)/([^\s\]\|<\n]+)"
)


def normalize_url(raw: str) -> str:
    """生のリンクテキストを正規化してURLスラグ形式に変換する。"""
    url = raw.strip()

    # 先頭の * を除去
    if url.startswith("*"):
        url = url[1:]

    # ドメイン部を除去してパスのみ残す
    url = DOMAIN_PATTERN.sub("", url)

    # 先頭の / を除去
    url = url.lstrip("/")

    # ハッシュ除去
    url = re.sub(r"#.*$", "", url)

    # マジックURI除去
    url = MAGIC_URI_PATTERN.sub("", url)

    # 末尾スラッシュ除去
    url = url.rstrip("/")

    # 小文字化
    url = url.lower()

    # スペース → - 変換
    url = url.replace(" ", "-")

    return url


def extract_links_from_file(content: str) -> list[str]:
    """ftmlコンテンツからリンク先URLスラグのリストを返す。"""
    found = []

    # パターンA
    for m in PATTERN_A.finditer(content):
        raw = m.group(1).strip()
        normalized = normalize_url(raw)
        found.append(normalized)

    # パターンB
    for m in PATTERN_B.finditer(content):
        raw = m.group(1)
        normalized = normalize_url(raw)
        found.append(normalized)

    # パターンC（パターンBと重複しないように、単純な裸URLのみ対象）
    for m in PATTERN_C.finditer(content):
        raw = m.group(1)
        normalized = normalize_url(raw)
        found.append(normalized)

    return found


def parse_file_list(path: str) -> list[dict]:
    """file-list.csv を読み込む。
    著者フィールドが {author1,author2} 形式でカンマを含む場合があるため、
    行全体を正規表現でパースする。
    """
    # url,title,author,createdAt の4フィールド。
    # author は {..} で囲まれたカンマ含み文字列の場合がある。
    # 先頭から url(,) title(,) author(,) createdAt として最後の / 区切り日付を末尾から取る。
    entries = []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    header = True
    for line in lines:
        line = line.rstrip("\n")
        if header:
            header = False
            continue
        if not line.strip() or line.strip().startswith("#"):
            continue

        # 末尾から createdAt（YYYY/M/D 形式）を取り出す
        # 例: "arukikata-kai,「SCP財団」の歩き方 改,{KABOOM1103,Tetsu1,...},2024/5/8"
        date_match = re.search(r",(\d{4}/\d{1,2}/\d{1,2})$", line)
        if not date_match:
            continue
        created_at = date_match.group(1)
        rest = line[: date_match.start()]  # url,title,author 部分

        # url は先頭のカンマ区切り1つ目
        first_comma = rest.index(",")
        url_slug = rest[:first_comma].strip()
        rest2 = rest[first_comma + 1 :]  # title,author 部分

        # title は次のカンマまで（著者は残り全部）
        second_comma = rest2.index(",")
        title = rest2[:second_comma].strip()
        author = rest2[second_comma + 1 :].strip()

        entries.append(
            {
                "url": url_slug,
                "title": title,
                "author": author,
                "createdAt": created_at,
            }
        )
    return entries


def scan_links(
    ftml_entries: list[tuple[str, str]], known_urls: set[str]
) -> dict[tuple[str, str], int]:
    """ftmlエントリからリンクカウントを返す。"""
    link_counts: dict[tuple[str, str], int] = defaultdict(int)
    for source, ftml_path in ftml_entries:
        with open(ftml_path, encoding="utf-8") as f:
            content = f.read()
        for target in extract_links_from_file(content):
            if target in known_urls and target != source:
                link_counts[(source, target)] += 1
    return link_counts


def main() -> None:
    # file-list.csv を読み込み
    all_nodes: list[dict] = []
    known_urls: set[str] = set()
    for row in parse_file_list(FILE_LIST):
        url_slug = row["url"].strip()
        if not url_slug or url_slug.startswith("#"):
            continue
        created_at = row["createdAt"].strip()
        year = int(created_at.split("/")[0])
        all_nodes.append(
            {
                "id": url_slug,
                "title": row["title"].strip(),
                "url": f"http://scp-jp.wikidot.com/{url_slug}",
                "author": row["author"].strip(),
                "createdAt": created_at,
                "year": year,
            }
        )
        known_urls.add(url_slug)

    # ftml/ が存在しなければ作成し、ftml/ 外の引数ファイルを移動
    arg_paths = sys.argv[1:]
    if arg_paths:
        os.makedirs(FTML_DIR, exist_ok=True)
        moved = []
        for path in arg_paths:
            dest = os.path.join(FTML_DIR, os.path.basename(path))
            if os.path.abspath(path) != os.path.abspath(dest):
                os.rename(path, dest)
                print(f"移動: {path} → {dest}")
            moved.append(dest)
        arg_paths = moved

    # 処理対象の (slug, ftml_path) リストを構築
    ftml_entries: list[tuple[str, str]] = []
    if arg_paths:
        for ftml_path in arg_paths:
            slug = os.path.splitext(os.path.basename(ftml_path))[0]
            if slug not in known_urls:
                print(f"警告: {slug} はfile-list.csvに含まれていません。スキップします。")
                continue
            if not os.path.exists(ftml_path):
                print(f"警告: {ftml_path} が見つかりません。スキップします。")
                continue
            ftml_entries.append((slug, ftml_path))
    elif os.path.isdir(FTML_DIR):
        for node in all_nodes:
            ftml_path = os.path.join(FTML_DIR, f"{node['id']}.ftml")
            if os.path.exists(ftml_path):
                ftml_entries.append((node["id"], ftml_path))
    else:
        print(f"警告: {FTML_DIR} が見つかりません。リンクなしで続行します。")

    # data.json が存在し引数指定ありの場合はインクリメンタル更新
    incremental = arg_paths and os.path.exists(OUTPUT)
    if incremental:
        with open(OUTPUT, encoding="utf-8") as f:
            existing = json.load(f)

        target_slugs = {slug for slug, _ in ftml_entries}
        # 既存ノードを維持しつつ対象ノードのみ更新
        nodes_by_id = {n["id"]: n for n in existing["nodes"]}
        nodes_by_id.update({n["id"]: n for n in all_nodes if n["id"] in target_slugs})
        nodes = list(nodes_by_id.values())

        # 対象slugのリンクを除いて既存リンクを保持し、再スキャン結果をマージ
        link_counts: dict[tuple[str, str], int] = defaultdict(int)
        for link in existing["links"]:
            if link["source"] not in target_slugs:
                link_counts[(link["source"], link["target"])] = link["count"]
        link_counts.update(scan_links(ftml_entries, known_urls))
    else:
        nodes = all_nodes
        link_counts = scan_links(ftml_entries, known_urls)

    links = [
        {"source": src, "target": tgt, "count": cnt}
        for (src, tgt), cnt in sorted(link_counts.items())
    ]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes, "links": links}, f, ensure_ascii=False, indent=2)

    print(f"nodes: {len(nodes)}, links: {len(links)}")
    print("data.json を更新しました。" if incremental else "data.json を生成しました。")


if __name__ == "__main__":
    main()
