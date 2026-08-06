"""
Groqが生成したキーワード群を使って、ファイル一覧を通常の文字列処理(部分一致)で絞り込む。
ここは完全にコードのみで完結し、LLMは使わない。

全角/半角・大文字小文字のゆれを吸収するため、比較前に unicodedata.normalize("NFKC", ...)
と lower() をかけておく。
"""
import datetime
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


def build_shallow_zip_paths(items: list[dict]) -> dict[str, str]:
    """
    ダウンロードZIP用に、深いSharePointのパスを浅い階層に変換する。
    「展開して1クリックでファイルに到達する」程度の浅さを目標にする。

    - 元のフォルダが1種類だけなら、ZIP直下にファイルを並べる(階層なし)
    - 元のフォルダが複数種類にまたがる場合だけ、フォルダ名(最後の1階層分、
      重複したらもう1階層足して曖昧さを解消)でグルーピングして分ける
    """
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        parent = item["path"].rsplit("/", 1)[0]
        groups[parent].append(item)

    result: dict[str, str] = {}

    if len(groups) <= 1:
        for item in items:
            result[item["path"]] = item["name"]
        return result

    labels: dict[str, str] = {}
    used_labels: set[str] = set()
    for parent in groups:
        parts = [p for p in parent.split("/") if p]
        depth = 1
        label = parts[-1] if parts else "root"
        while label in used_labels and depth < len(parts):
            depth += 1
            label = "_".join(parts[-depth:])
        used_labels.add(label)
        labels[parent] = label

    for parent, group_items in groups.items():
        label = labels[parent]
        for item in group_items:
            result[item["path"]] = f"{label}/{item['name']}"

    return result


def exclude_keyword(files: list[dict], keyword: str) -> list[dict]:
    """
    ファイルの path にkeywordを含むものを除外する(NOT条件)。
    「再試」のラベルが無いものをデフォルトで「本試」とみなす、といった
    「ラベルが無いものを残したい」場面で使う(ANDのOR条件では表現できないため別関数にしている)。
    """
    if not keyword:
        return files
    kw = _normalize(keyword)
    return [f for f in files if kw not in _normalize(f["path"])]


def require_keyword(files: list[dict], keyword: str) -> list[dict]:
    """ファイルの path にkeywordを含むものだけを残す(単純な部分一致の絞り込み)。"""
    if not keyword:
        return files
    kw = _normalize(keyword)
    return [f for f in files if kw in _normalize(f["path"])]


def build_fixed_element_groups(
    years_back: int | None,
    semester: str,
    file_type: str,
    current_year: int | None = None,
) -> tuple[list[dict], int | None, int]:
    """
    「年数」「前期/後期」「種別」のように、フォルダ構成に依存せず固定で扱える
    条件だけをAND条件グループに変換する。
    「分野」「担当科」はフォルダ構成ごとに違うため、facets.build_element_groups_from_selections
    の方で別途扱う(呼び出し側でこの関数の結果と合体させる)。

    戻り値: (element_groups, 年範囲の開始年 or None, 基準年)
    """
    if current_year is None:
        current_year = datetime.datetime.now().year

    groups: list[dict] = []
    start_year = None

    if years_back and years_back > 0:
        start_year = current_year - years_back + 1
        years = [str(y) for y in range(start_year, current_year + 1)]
        groups.append({"category": "期間(ファイル名の年)", "keywords": years})

    if semester:
        groups.append({"category": "前期/後期", "keywords": [semester]})

    if file_type == "kakomon":
        groups.append({"category": "種別", "keywords": ["過去問"]})
    elif file_type == "kaisetsu":
        groups.append({"category": "種別", "keywords": ["解説"]})
    elif file_type == "both":
        groups.append({"category": "種別", "keywords": ["過去問", "解説"]})
    # file_type が空/未知の値の場合は種別条件を付けない

    return groups, start_year, current_year
