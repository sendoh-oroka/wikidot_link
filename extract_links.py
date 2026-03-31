# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

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

# 平仮名・片仮名・アットマークの検出用（URLエンコードされたものはマッチしない）
INVALID_CHARS_PATTERN = re.compile(r"[\u3040-\u309F\u30A0-\u30FF@]")

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


def is_valid_url(url: str) -> bool:
    """正規化されたURLスラグが保存対象として適切か判定する。"""
    if not url:
        return False
    
    # 対象ドメイン以外の外部リンクを除外
    if url.startswith("http://") or url.startswith("https://"):
        return False
        
    # 指定されたパス空間の除外
    if url.startswith("system:page-tags") or url.startswith("forum/"):
        return False
        
    # 平仮名、片仮名、アットマークを含むものを除外
    if INVALID_CHARS_PATTERN.search(url):
        return False
        
    return True


def extract_links_from_file(content: str) -> list[str]:
    """ftmlコンテンツからリンク先URLスラグのリストを返す。"""
    found = []

    # パターンA
    for m in PATTERN_A.finditer(content):
        raw = m.group(1).strip()
        normalized = normalize_url(raw)
        if is_valid_url(normalized):
            found.append(normalized)

    # パターンB
    for m in PATTERN_B.finditer(content):
        raw = m.group(1)
        normalized = normalize_url(raw)
        if is_valid_url(normalized):
            found.append(normalized)

    # パターンC（パターンBと重複しないように、単純な裸URLのみ対象）
    for m in PATTERN_C.finditer(content):
        raw = m.group(1)
        normalized = normalize_url(raw)
        if is_valid_url(normalized):
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
) -> tuple[dict[tuple[str, str], int], dict[str, dict[str, int]]]:
    """ftmlエントリからリンクカウントと全抽出リンクを返す。"""
    link_counts: dict[tuple[str, str], int] = defaultdict(int)
    extracted: dict[str, dict[str, int]] = {}
    for source, ftml_path in ftml_entries:
        with open(ftml_path, encoding="utf-8") as f:
            content = f.read()
        links = extract_links_from_file(content)
        counts: dict[str, int] = defaultdict(int)
        for target in links:
            counts[target] += 1

        # すべての抽出リンクを保存
        extracted[source] = dict(counts)

        # 既知のURLへのリンクのみカウント
        for target, cnt in counts.items():
            if target in known_urls and target != source:
                link_counts[(source, target)] += cnt

    return link_counts, extracted


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

    # 引数で渡されたファイルの処理（フィルタリングと移動）
    arg_paths = sys.argv[1:]
    valid_args: list[tuple[str, str]] = []
    
    if arg_paths:
        os.makedirs(FTML_DIR, exist_ok=True)
        for path in arg_paths:
            slug = os.path.splitext(os.path.basename(path))[0]
            if slug not in known_urls:
                print(f"警告: {slug} はfile-list.csvに含まれていません。移動・処理をスキップします。")
                continue

            dest = os.path.join(FTML_DIR, os.path.basename(path))
            # ftml/以下のファイルが渡された場合など、移動元と先が同じなら移動しない
            if os.path.abspath(path) != os.path.abspath(dest):
                if os.path.exists(path):
                    os.rename(path, dest)
                    print(f"移動: {path} → {dest}")
                else:
                    print(f"警告: {path} が見つかりません。スキップします。")
                    continue
            valid_args.append((slug, dest))

    # 処理対象の (slug, ftml_path) リストを構築
    ftml_entries: list[tuple[str, str]] = []
    if arg_paths:
        for slug, ftml_path in valid_args:
            if os.path.exists(ftml_path):
                ftml_entries.append((slug, ftml_path))
    elif os.path.isdir(FTML_DIR):
        for node in all_nodes:
            ftml_path = os.path.join(FTML_DIR, f"{node['id']}.ftml")
            if os.path.exists(ftml_path):
                ftml_entries.append((node["id"], ftml_path))
    else:
        print(f"警告: {FTML_DIR} が見つかりません。リンクなしで続行します。")

    # data.json が存在する場合は常に既存のデータを考慮する（引数なしの全体実行時も含む）
    incremental = os.path.exists(OUTPUT)

    if incremental:
        with open(OUTPUT, encoding="utf-8") as f:
            existing = json.load(f)

        target_slugs = {slug for slug, _ in ftml_entries}

        # 既存ノードを維持 (file-listから削除されたものを除外するために known_urls を確認)
        nodes_by_id = {n["id"]: n for n in existing.get("nodes", []) if n["id"] in known_urls}
        
        # 新規・更新ファイルからリンクをスキャン
        new_link_counts, new_extracted = scan_links(ftml_entries, known_urls)

        for n in all_nodes:
            slug = n["id"]
            if slug in target_slugs:
                # 引数で更新された、またはftmlが存在するノード
                n["extracted_links"] = new_extracted.get(slug, {})
            elif slug in nodes_by_id:
                # ftmlが存在しないが、既存データがあるノード（情報を維持）
                n["extracted_links"] = nodes_by_id[slug].get("extracted_links", {})
            else:
                # ftmlが存在せず、既存データもない新規ノード
                n["extracted_links"] = {}
                
            nodes_by_id[slug] = n

        nodes = list(nodes_by_id.values())

        link_counts: dict[tuple[str, str], int] = defaultdict(int)
        
        # 1. 更新対象外ノードの既存リンクを維持
        for link in existing.get("links", []):
            src, tgt = link["source"], link["target"]
            if src not in target_slugs and src in known_urls and tgt in known_urls:
                link_counts[(src, tgt)] = link["count"]
                
        # 2. 引数対象ノードの新しいリンクを追加
        for (src, tgt), cnt in new_link_counts.items():
            link_counts[(src, tgt)] = cnt

        # 3. 引数対象ノードに向けられた「既存ノードからの被リンク」を更新
        # data.json に保持している extracted_links を使い、既存ノードの参照を再現
        for node in nodes_by_id.values():
            src = node["id"]
            if src in target_slugs:
                continue
            extracted = node.get("extracted_links", {})
            for tgt, cnt in extracted.items():
                if tgt in target_slugs and tgt != src:
                    link_counts[(src, tgt)] = cnt

    else:
        # フル更新
        link_counts, new_extracted = scan_links(ftml_entries, known_urls)
        nodes = []
        for n in all_nodes:
            slug = n["id"]
            n["extracted_links"] = new_extracted.get(slug, {})
            nodes.append(n)

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
