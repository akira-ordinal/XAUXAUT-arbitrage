"""
config.py
---------
環境変数(.env)からXAUUSDT / XAUTUSDT 先物ペアトレードボットの設定を読み込む。

このバージョンは「cronで1分ごとに1回起動し、状態をファイルに保存して終了する」
実行モデル(ラッコサーバー等の共用レンタルサーバー向け)を前提にしている。
常駐ループ(while True)は行わない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


@dataclass
class Config:
    # --- Bitget API 認証情報 ---
    api_key: str = os.getenv("BITGET_API_KEY", "")
    api_secret: str = os.getenv("BITGET_API_SECRET", "")
    passphrase: str = os.getenv("BITGET_PASSPHRASE", "")

    # --- 実行モード ---
    dry_run: bool = _get_bool("DRY_RUN", True)

    # --- 対象シンボル(先物, USDT-M Perpetual) ---
    symbol_a: str = os.getenv("SYMBOL_A", "XAUUSDT")
    symbol_b: str = os.getenv("SYMBOL_B", "XAUTUSDT")
    product_type: str = os.getenv("PRODUCT_TYPE", "USDT-FUTURES")
    margin_coin: str = os.getenv("MARGIN_COIN", "USDT")

    # --- 証拠金 / レバレッジ ---
    margin_mode: str = os.getenv("MARGIN_MODE", "isolated")
    leverage: str = os.getenv("LEVERAGE", "3")

    # --- 戦略パラメータ ---
    entry_threshold_pct: float = _get_float("ENTRY_THRESHOLD_PCT", 0.0015)
    exit_threshold_pct: float = _get_float("EXIT_THRESHOLD_PCT", 0.0003)
    stop_loss_spread_pct: float = _get_float("STOP_LOSS_SPREAD_PCT", 0.006)
    max_hold_seconds: int = _get_int("MAX_HOLD_SECONDS", 6 * 3600)
    taker_fee_pct: float = _get_float("TAKER_FEE_PCT", 0.0006)

    # --- サイジング / 運用制限 ---
    trade_notional_usdt: float = _get_float("TRADE_NOTIONAL_USDT", 100.0)
    min_trade_notional_usdt: float = _get_float("MIN_TRADE_NOTIONAL_USDT", 20.0)
    max_trades_per_day: int = _get_int("MAX_TRADES_PER_DAY", 6)

    # --- 追加の安全装置 ---
    max_loss_usdt: float = _get_float("MAX_LOSS_USDT", 15.0)

    # --- サイズ丸め ---
    size_decimals_a: int = _get_int("SIZE_DECIMALS_A", 2)
    size_decimals_b: int = _get_int("SIZE_DECIMALS_B", 2)

    # --- cron実行/状態保存まわり ---
    # 状態(保有ポジション・当日取引回数等)を保存するJSONファイル
    state_file_path: str = os.getenv("STATE_FILE_PATH", "state.json")
    # 多重起動防止用のロックファイル
    lock_file_path: str = os.getenv("LOCK_FILE_PATH", "bot.lock")
    # 1回のcron実行がこの秒数を超えたら異常とみなしログに警告を出す(cronの実行間隔より少し短く)
    max_run_seconds: float = _get_float("MAX_RUN_SECONDS", 45.0)

    # --- ログ ---
    trade_log_path: str = os.getenv("TRADE_LOG_PATH", "trades.csv")
    app_log_path: str = os.getenv("APP_LOG_PATH", "bot.log")

    def validate(self) -> None:
        if not self.dry_run and not (self.api_key and self.api_secret and self.passphrase):
            raise ValueError(
                "DRY_RUN=false で実際に発注する場合は BITGET_API_KEY / BITGET_API_SECRET / "
                "BITGET_PASSPHRASE を必ず設定してください。"
            )
        if self.entry_threshold_pct <= self.exit_threshold_pct:
            raise ValueError("ENTRY_THRESHOLD_PCT は EXIT_THRESHOLD_PCT より大きくしてください。")
        if self.trade_notional_usdt < self.min_trade_notional_usdt:
            raise ValueError("TRADE_NOTIONAL_USDT は MIN_TRADE_NOTIONAL_USDT 以上にしてください。")
        if float(self.leverage) > 5:
            raise ValueError(
                "LEVERAGE が5倍を超えています。意図的な場合はこのチェックをconfig.py側で調整してください。"
            )
