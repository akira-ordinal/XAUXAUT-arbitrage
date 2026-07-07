"""
state_store.py
---------------
cronは1回起動するたびにプロセスが終了するため、保有ポジションや当日取引回数などの
状態はメモリに残せない。そのため、実行のたびにJSONファイルへ読み書きして状態を
引き継ぐ。

保存する内容:
  - position: 現在保有中のペアポジション(なければ null)
  - trades_today: 当日のエントリー回数
  - day_marker: trades_todayの基準日(YYYY-MM-DD)
  - account_configured: set-leverage/set-margin-modeを設定済みかどうか
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional


DEFAULT_STATE = {
    "position": None,
    "trades_today": 0,
    "day_marker": time.strftime("%Y-%m-%d"),
    "account_configured": False,
}


class StateStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return dict(DEFAULT_STATE)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # 破損していた場合は安全側に倒して初期状態から始める
            return dict(DEFAULT_STATE)
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        return merged

    def save(self, state: dict) -> None:
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)  # atomic rename(破損防止)
