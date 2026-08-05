"""
Groqが生成したキーワード群を使って、ファイル一覧を通常の文字列処理(部分一致)で絞り込む。
ここは完全にコードのみで完結し、LLMは使わない。

全角/半角・大文字小文字のゆれを吸収するため、比較前に unicodedata.normalize("NFKC", ...)
と lower() をかけておく。
"""
import unicodedata


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKC", s).lower()


def filter_by_keywords(files: list[dict], keywords: list[str]) -> list[dict]:
    """
    ファイルの path にキーワードのいずれかが部分一致すれば候補として残す。
    キーワードが空の場合は絞り込まず全件返す(呼び出し側でフォールバックとして利用)。
    """
    if not keywords:
        return files

    normalized_keywords = [_normalize(k) for k in keywords if k.strip()]
    if not normalized_keywords:
        return files

    matched = []
    for f in files:
        normalized_path = _normalize(f["path"])
        if any(kw in normalized_path for kw in normalized_keywords):
            matched.append(f)
    return matched


def filter_by_element_groups(files: list[dict], element_groups: list[dict]) -> list[dict]:
    """
    element_groups 例:
        [{"category": "分野", "keywords": ["腎臓内科", "腎内"]},
         {"category": "種類", "keywords": ["過去問"]}]

    各グループ内はOR(いずれかのキーワードが一致すればOK)、
    グループ間はAND(すべてのグループを満たす必要あり)で path をマッチする。
    element_groups が空の場合は絞り込まず全件返す。
    """
    if not element_groups:
        return files

    normalized_groups = []
    for group in element_groups:
        kws = [_normalize(k) for k in group.get("keywords", []) if k.strip()]
        if kws:
            normalized_groups.append(kws)

    if not normalized_groups:
        return files

    matched = []
    for f in files:
        normalized_path = _normalize(f["path"])
        if all(any(kw in normalized_path for kw in group) for group in normalized_groups):
            matched.append(f)
    return matched


def derive_folder_prefixes(matched_files: list[dict]) -> set[str]:
    """マッチしたファイルそれぞれの親フォルダのパスを取り出す。"""
    prefixes = set()
    for f in matched_files:
        parent = f["path"].rsplit("/", 1)[0]
        prefixes.add(parent)
    return prefixes


def expand_by_prefixes(all_files: list[dict], prefixes: set[str]) -> list[dict]:
    """
    指定したフォルダ(prefix群)配下に属する全ファイルを、元のツリー全体から取り出す。
    個々のファイル名にキーワードが含まれていなくても、該当フォルダの中身なら丸ごと拾う。
    """
    if not prefixes:
        return []
    return [f for f in all_files if any(f["path"].startswith(p) for p in prefixes)]
