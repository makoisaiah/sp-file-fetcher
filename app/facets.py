"""
フォルダ構造から「検索フォームの選択肢」を作るためのモジュール。

- extract_candidate_names(): コードのみ(頻度分析)で候補フォルダ名を抽出する。
  ここで候補を数百件程度まで絞ってから初めてAIに渡すことで、
  「木構造全体をAIに渡す」事態を避ける。
- 実際に「分野」「担当科」「ノイズ」に仕分けるのは意味理解が必要なので
  groq_select.classify_facets() (1回だけ呼ぶ、結果はキャッシュ)に任せる。
- 仕分け結果はローカルJSONにキャッシュし、人手での編集(手直し)もそのまま
  ファイルを直接編集/保存し直せる形にしてある。
"""
import json
import os
import time
from collections import defaultdict

MAX_CANDIDATES = 300  # これを超えたら先頭で打ち切る(AIへの負荷を抑えるため)


def _build_folder_children(files: list[dict]) -> dict[str, set[str]]:
    """親フォルダのパス -> 直下の子フォルダ名の集合、を全ファイルパスから再構築する。"""
    folder_children: dict[str, set[str]] = defaultdict(set)
    for f in files:
        parts = f["path"].strip("/").split("/")
        folders = parts[:-1]  # 末尾はファイル名なので除く
        prefix = ""
        for name in folders:
            parent = prefix
            prefix = prefix + "/" + name
            folder_children[parent].add(name)
    return folder_children


def extract_candidate_names(files: list[dict]) -> list[str]:
    """
    2種類のヒューリスティックでフォルダ名候補を集める(いずれもコードのみ):

    1. 複数の異なる親フォルダの下で使い回されている名前
       → 「担当科」のような属性っぽい名前(内科・外科・病理 など)
    2. 兄弟フォルダが多い(メニューのような)場所にだけ現れる固有の名前
       → 「分野」のような具体的なテーマ名(腎泌尿器・循環器 など)

    両方合わせても、日付・人名・運用メモなどのノイズが混ざるため、
    最終的な仕分けは groq_select.classify_facets() に任せる。
    """
    folder_children = _build_folder_children(files)

    name_to_parents: dict[str, set[str]] = defaultdict(set)
    for parent, children in folder_children.items():
        for child in children:
            name_to_parents[child].add(parent)

    candidates: set[str] = set()

    for name, parents in name_to_parents.items():
        if len(parents) >= 2:
            candidates.add(name)

    for parent, children in folder_children.items():
        if len(children) >= 4:
            for c in children:
                if len(name_to_parents[c]) == 1:
                    candidates.add(c)

    candidates_list = sorted(candidates)
    return candidates_list[:MAX_CANDIDATES]


def save_facet_cache(cache_path: str, facets: dict) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"saved_at": time.time(), "facets": facets}, f, ensure_ascii=False, indent=2)


def load_facet_cache(cache_path: str) -> dict | None:
    if not os.path.exists(cache_path):
        return None
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("facets")


def build_element_groups_from_selections(
    selections: dict[str, list[str]], facet_cache: dict
) -> list[dict]:
    """
    selections 例: {"分野": ["腎・泌尿器"], "担当科": ["内科"]}
    facet_cache 例: {"分野": [{"label": "腎・泌尿器", "keywords": ["腎泌尿器", "腎・泌尿器"]}, ...], ...}

    選択されたlabelに対応するkeywordsを集め、カテゴリごとにORグループを作る
    (filter_by_element_groups にそのまま渡せる形式)。
    """
    groups = []
    for category, labels in selections.items():
        if not labels:
            continue
        options = facet_cache.get(category, [])
        label_to_keywords = {opt["label"]: opt.get("keywords", []) for opt in options}
        keywords: list[str] = []
        for label in labels:
            keywords.extend(label_to_keywords.get(label, [label]))
        if keywords:
            groups.append({"category": category, "keywords": keywords})
    return groups
