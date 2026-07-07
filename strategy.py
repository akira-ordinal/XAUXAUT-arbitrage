"""
strategy.py
-----------
XAUUSDT(ゴールド指数パーペチュアル)と XAUTUSDT(Tether Goldパーペチュアル)の
価格差を監視し、一定以上の乖離が生じたときに「割高な方をショート・割安な方をロング」
の両建てを同時に行う市場中立型ペアトレード(ベーシス取引)戦略。

【実行モデルについて】
このバージョンは cron によって「1分ごとに1回起動され、1回だけ判定・発注を行って
終了する」ことを前提にしている(常駐ループではない)。そのため、保有ポジション等の
状態は毎回 state_store.StateStore を通じてJSONファイルに保存・復元する。

現物版(BTC/WBTC)との違い:
  先物なのでショートが可能。理論上は同時に両建てすることで、ゴールド価格そのものの
  変動(方向性リスク)を大きく相殺しつつ、2銘柄間のスプレッドのみに賭けることができる。

それでも残るリスク(必ず理解してから使ってください):
  1. 完全なマーケットニュートラルではない。XAUUSDT(ゴールド指数)と XAUTUSDT(XAUT現物
     連動)は算出方法が異なるため、値動きに微妙なズレ(トラッキング差)が生じ得る。
  2. レバレッジ商品のため、証拠金不足・急激な変動により清算(liquidation)されるリスクが
     ロング・ショート双方の脚に存在する。isolatedモードでも脚ごとに証拠金不足になり得る。
  3. ファンディングレート: ロングとショートで受け払いが発生し、両者のファンディング
     レート差が想定外のコスト(またはボーナス)になる。本ボットはファンディングを
     戦略判断には織り込んでいない。
  4. 2脚の注文は同時ではなく逐次発注のため、片方だけ約定してもう片方が失敗する
     「脚の欠落(レッグリスク)」が起こり得る。本ボットは失敗時に残った脚を
     即座に手仕舞いするが、その間の価格変動リスクはゼロにはできない。
  5. 板が薄い時間帯はスリッページにより理論上のスプレッドが利益に直結しない。
  6. cronは1分間隔での実行のため、1分未満で開いて閉じるような短命なスプレッドは
     検知できない場合がある。

必ず DRY_RUN=True で十分に検証し、少額・低レバレッジから始めてください。
"""

from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests

from bitget_client import BitgetClient, BitgetAPIError
from config import Config
from state_store import StateStore

logger = logging.getLogger("arb_bot")


@dataclass
class Leg:
    symbol: str
    side: str          # "buy"(ロング) or "sell"(ショート)
    qty: float
    entry_price: float

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Leg":
        return Leg(symbol=d["symbol"], side=d["side"], qty=d["qty"], entry_price=d["entry_price"])


@dataclass
class PairPosition:
    long_leg: Leg
    short_leg: Leg
    entry_time: float
    entry_spread_pct: float

    def to_dict(self) -> dict:
        return {
            "long_leg": self.long_leg.to_dict(),
            "short_leg": self.short_leg.to_dict(),
            "entry_time": self.entry_time,
            "entry_spread_pct": self.entry_spread_pct,
        }

    @staticmethod
    def from_dict(d: dict) -> "PairPosition":
        return PairPosition(
            long_leg=Leg.from_dict(d["long_leg"]),
            short_leg=Leg.from_dict(d["short_leg"]),
            entry_time=d["entry_time"],
            entry_spread_pct=d["entry_spread_pct"],
        )


@dataclass
class Prices:
    bid_a: float
    ask_a: float
    bid_b: float
    ask_b: float

    @property
    def mid_a(self) -> float:
        return (self.bid_a + self.ask_a) / 2

    @property
    def mid_b(self) -> float:
        return (self.bid_b + self.ask_b) / 2


class ArbitrageBot:
    def __init__(self, cfg: Config):
        cfg.validate()
        self.cfg = cfg
        self.client = BitgetClient(cfg.api_key, cfg.api_secret, cfg.passphrase)
        self.state_store = StateStore(cfg.state_file_path)

        state = self.state_store.load()
        self.position: Optional[PairPosition] = (
            PairPosition.from_dict(state["position"]) if state.get("position") else None
        )
        self.trades_today: int = state.get("trades_today", 0)
        self.day_marker: str = state.get("day_marker", time.strftime("%Y-%m-%d"))
        self.account_configured: bool = state.get("account_configured", False)

        self._init_trade_log()

    # ------------------------------------------------------------------
    # 状態の保存
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        state = {
            "position": self.position.to_dict() if self.position else None,
            "trades_today": self.trades_today,
            "day_marker": self.day_marker,
            "account_configured": self.account_configured,
        }
        self.state_store.save(state)

    # ------------------------------------------------------------------
    # 起動時セットアップ(初回のみ実行、以降は状態フラグでスキップ)
    # ------------------------------------------------------------------

    def ensure_account_settings(self) -> None:
        if self.account_configured:
            return
        if self.cfg.dry_run:
            logger.info(
                "[DRY-RUN] set-margin-mode/set-leverage はスキップ (margin_mode=%s, leverage=%s)",
                self.cfg.margin_mode, self.cfg.leverage,
            )
            self.account_configured = True
            return
        for symbol in (self.cfg.symbol_a, self.cfg.symbol_b):
            self.client.set_margin_mode(symbol, self.cfg.margin_mode, self.cfg.product_type, self.cfg.margin_coin)
            self.client.set_leverage(symbol, self.cfg.leverage, self.cfg.product_type, self.cfg.margin_coin)
            logger.info("設定完了: %s margin_mode=%s leverage=%s", symbol, self.cfg.margin_mode, self.cfg.leverage)
        self.account_configured = True

    # ------------------------------------------------------------------
    # 価格取得
    # ------------------------------------------------------------------

    def fetch_prices(self) -> Prices:
        t_a = self.client.get_futures_ticker(self.cfg.symbol_a, self.cfg.product_type)
        t_b = self.client.get_futures_ticker(self.cfg.symbol_b, self.cfg.product_type)
        return Prices(
            bid_a=float(t_a["bidPr"]),
            ask_a=float(t_a["askPr"]),
            bid_b=float(t_b["bidPr"]),
            ask_b=float(t_b["askPr"]),
        )

    @staticmethod
    def spread_pct(mid_target: float, mid_ref: float) -> float:
        return (mid_target - mid_ref) / mid_ref

    # ------------------------------------------------------------------
    # 発注ヘルパー(dry_run分岐込み)
    # ------------------------------------------------------------------

    def _round_qty(self, symbol: str, qty: float) -> float:
        decimals = self.cfg.size_decimals_a if symbol == self.cfg.symbol_a else self.cfg.size_decimals_b
        return round(qty, decimals)

    def _open_leg(self, symbol: str, side: str, notional_usdt: float, ref_price: float) -> Leg:
        qty = self._round_qty(symbol, notional_usdt / ref_price)
        if self.cfg.dry_run:
            logger.info(
                "[DRY-RUN] OPEN %s %s  qty=%.4f  想定価格=%.3f  notional=%.2f USDT",
                side.upper(), symbol, qty, ref_price, notional_usdt,
            )
            return Leg(symbol=symbol, side=side, qty=qty, entry_price=ref_price)

        result = self.client.place_futures_order(
            symbol=symbol, side=side, size=f"{qty}", order_type="market",
            product_type=self.cfg.product_type, margin_mode=self.cfg.margin_mode,
            margin_coin=self.cfg.margin_coin, reduce_only=False,
        )
        order_info = self._poll_order(symbol, result.get("orderId"))
        filled_price = float(order_info.get("priceAvg", ref_price)) or ref_price
        filled_qty = float(order_info.get("baseVolume", qty)) or qty
        logger.info("OPEN 約定: %s %s qty=%.4f price=%.3f orderId=%s", side.upper(), symbol, filled_qty, filled_price, result.get("orderId"))
        return Leg(symbol=symbol, side=side, qty=filled_qty, entry_price=filled_price)

    def _close_leg(self, leg: Leg, ref_price: float) -> float:
        close_side = "sell" if leg.side == "buy" else "buy"
        if self.cfg.dry_run:
            logger.info(
                "[DRY-RUN] CLOSE %s %s  qty=%.4f  想定価格=%.3f",
                close_side.upper(), leg.symbol, leg.qty, ref_price,
            )
            return ref_price

        result = self.client.place_futures_order(
            symbol=leg.symbol, side=close_side, size=f"{leg.qty}", order_type="market",
            product_type=self.cfg.product_type, margin_mode=self.cfg.margin_mode,
            margin_coin=self.cfg.margin_coin, reduce_only=True,
        )
        order_info = self._poll_order(leg.symbol, result.get("orderId"))
        filled_price = float(order_info.get("priceAvg", ref_price)) or ref_price
        logger.info("CLOSE 約定: %s %s qty=%.4f price=%.3f orderId=%s", close_side.upper(), leg.symbol, leg.qty, filled_price, result.get("orderId"))
        return filled_price

    def _poll_order(self, symbol: str, order_id: Optional[str], timeout: float = 10.0, interval: float = 0.5) -> dict:
        if not order_id:
            return {}
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            try:
                info = self.client.get_futures_order_info(symbol, order_id=order_id, product_type=self.cfg.product_type)
                last = info
                if info.get("state") in ("filled", "full_fill"):
                    return info
            except BitgetAPIError as e:
                logger.warning("注文情報取得エラー: %s", e)
            time.sleep(interval)
        return last

    @staticmethod
    def _pnl(leg: Leg, current_price: float) -> float:
        if leg.side == "buy":
            return (current_price - leg.entry_price) * leg.qty
        return (leg.entry_price - current_price) * leg.qty

    # ------------------------------------------------------------------
    # ロギング
    # ------------------------------------------------------------------

    def _init_trade_log(self) -> None:
        path = self.cfg.trade_log_path
        is_new = not os.path.exists(path)
        self._log_file = open(path, "a", newline="", encoding="utf-8")
        self._log_writer = csv.writer(self._log_file)
        if is_new:
            self._log_writer.writerow(
                ["timestamp", "action", "long_symbol", "short_symbol", "spread_pct", "pnl_usdt", "reason", "dry_run"]
            )

    def _log_event(self, action: str, long_symbol: str, short_symbol: str, spread_pct: float,
                   pnl_usdt: Optional[float] = None, reason: str = "") -> None:
        self._log_writer.writerow(
            [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                action, long_symbol, short_symbol,
                f"{spread_pct:.5f}",
                f"{pnl_usdt:.4f}" if pnl_usdt is not None else "",
                reason,
                self.cfg.dry_run,
            ]
        )
        self._log_file.flush()

    def close(self) -> None:
        """呼び出し元(run_once.py)が最後に呼ぶクリーンアップ処理。"""
        self._log_file.close()

    # ------------------------------------------------------------------
    # メインロジック(1回分)
    # ------------------------------------------------------------------

    def _reset_daily_counter_if_needed(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self.day_marker:
            self.day_marker = today
            self.trades_today = 0

    def step(self) -> None:
        """cronから1回呼ばれるたびに実行されるメイン処理。"""
        self._reset_daily_counter_if_needed()
        self.ensure_account_settings()
        prices = self.fetch_prices()

        if self.position is None:
            self._maybe_enter(prices)
        else:
            self._maybe_exit(prices)

    def _maybe_enter(self, prices: Prices) -> None:
        if self.trades_today >= self.cfg.max_trades_per_day:
            logger.info("本日の最大取引回数に到達しているためエントリーをスキップ")
            return

        spr = self.spread_pct(prices.mid_b, prices.mid_a)

        if abs(spr) < self.cfg.entry_threshold_pct:
            logger.info(
                "spread=%.4f%%  (XAU mid=%.3f, XAUT mid=%.3f)  閾値未満のため待機",
                spr * 100, prices.mid_a, prices.mid_b,
            )
            return

        notional = self.cfg.trade_notional_usdt
        if notional < self.cfg.min_trade_notional_usdt:
            logger.warning("設定された取引名目額が小さすぎます")
            return

        if spr > 0:
            long_symbol, long_ref = self.cfg.symbol_a, prices.ask_a
            short_symbol, short_ref = self.cfg.symbol_b, prices.bid_b
        else:
            long_symbol, long_ref = self.cfg.symbol_b, prices.ask_b
            short_symbol, short_ref = self.cfg.symbol_a, prices.bid_a

        long_leg = self._open_leg(long_symbol, "buy", notional, long_ref)

        try:
            short_leg = self._open_leg(short_symbol, "sell", notional, short_ref)
        except (BitgetAPIError, requests.exceptions.RequestException, RuntimeError) as e:
            logger.error("ショート脚の発注に失敗したため、ロング脚を緊急手仕舞いします: %s", e)
            self._close_leg(long_leg, long_ref)
            self._log_event("ABORT_LEG_FAILURE", long_symbol, short_symbol, spr, reason=str(e))
            return

        self.position = PairPosition(long_leg=long_leg, short_leg=short_leg, entry_time=time.time(), entry_spread_pct=spr)
        self.trades_today += 1
        self._log_event("ENTER", long_symbol, short_symbol, spr)
        logger.info(
            "エントリー: LONG %s(qty=%.4f@%.3f) / SHORT %s(qty=%.4f@%.3f)  spread=%.4f%%",
            long_leg.symbol, long_leg.qty, long_leg.entry_price,
            short_leg.symbol, short_leg.qty, short_leg.entry_price, spr * 100,
        )

    def _maybe_exit(self, prices: Prices) -> None:
        pos = self.position
        assert pos is not None

        spr = self.spread_pct(prices.mid_b, prices.mid_a)
        held_time = time.time() - pos.entry_time

        def price_for(symbol: str, side: str) -> float:
            if symbol == self.cfg.symbol_a:
                return prices.bid_a if side == "buy" else prices.ask_a
            return prices.bid_b if side == "buy" else prices.ask_b

        long_current = price_for(pos.long_leg.symbol, "buy")
        short_current = price_for(pos.short_leg.symbol, "sell")

        pnl_long = self._pnl(pos.long_leg, long_current)
        pnl_short = self._pnl(pos.short_leg, short_current)
        total_pnl = pnl_long + pnl_short

        reason = None
        if abs(spr) <= self.cfg.exit_threshold_pct:
            reason = "take_profit_spread_converged"
        elif abs(spr) >= self.cfg.stop_loss_spread_pct and abs(spr) > abs(pos.entry_spread_pct):
            reason = "stop_loss_spread_widened"
        elif total_pnl <= -self.cfg.max_loss_usdt:
            reason = "stop_loss_absolute_pnl"
        elif held_time >= self.cfg.max_hold_seconds:
            reason = "max_hold_time_exceeded"

        if reason is None:
            logger.info(
                "保有中: LONG %s / SHORT %s  spread=%.4f%%  含み損益=%.2f USDT  経過=%.0fs",
                pos.long_leg.symbol, pos.short_leg.symbol, spr * 100, total_pnl, held_time,
            )
            return

        close_price_long = self._close_leg(pos.long_leg, long_current)
        close_price_short = self._close_leg(pos.short_leg, short_current)
        realized_pnl = (
            self._pnl(pos.long_leg, close_price_long) + self._pnl(pos.short_leg, close_price_short)
        )
        total_notional = pos.long_leg.qty * pos.long_leg.entry_price + pos.short_leg.qty * pos.short_leg.entry_price
        est_fees = total_notional * self.cfg.taker_fee_pct * 2
        realized_pnl -= est_fees

        self._log_event("EXIT", pos.long_leg.symbol, pos.short_leg.symbol, spr, pnl_usdt=realized_pnl, reason=reason)
        logger.info(
            "決済(%s): 実現損益(概算, 手数料込)=%.2f USDT  保有時間=%.0fs",
            reason, realized_pnl, held_time,
        )
        self.position = None
