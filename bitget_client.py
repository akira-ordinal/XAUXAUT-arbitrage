"""
bitget_client.py
----------------
Bitget 先物(USDT-M Perpetual, Mix API v2)の薄いRESTラッパー。

対応エンドポイント:
  - GET  /api/v2/mix/market/ticker           (公開)
  - GET  /api/v2/mix/market/contracts        (公開, 銘柄仕様確認用)
  - GET  /api/v2/mix/account/account         (認証, 証拠金/利用可能額)
  - GET  /api/v2/mix/position/single-position (認証, 保有ポジション)
  - POST /api/v2/mix/account/set-leverage    (認証)
  - POST /api/v2/mix/account/set-margin-mode (認証)
  - POST /api/v2/mix/order/place-order       (認証)
  - GET  /api/v2/mix/order/detail            (認証)

参考: https://www.bitget.com/api-doc/contract/intro

認証方式は現物と共通:
  ACCESS-SIGN = base64( HMAC_SHA256( secret, timestamp + METHOD + requestPath[+queryString or body] ) )
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Optional

import requests

BASE_URL = "https://api.bitget.com"


class BitgetAPIError(Exception):
    def __init__(self, code: str, msg: str, raw: Optional[dict] = None):
        self.code = code
        self.msg = msg
        self.raw = raw
        super().__init__(f"Bitget API error {code}: {msg}")


class BitgetClient:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        timeout: float = 10.0,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # 署名
    # ------------------------------------------------------------------

    @staticmethod
    def _timestamp_ms() -> str:
        return str(int(time.time() * 1000))

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        mac = hmac.new(self.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _auth_headers(self, timestamp: str, sign: str) -> dict:
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    @staticmethod
    def new_client_oid() -> str:
        return "bot" + uuid.uuid4().hex[:20]

    # ------------------------------------------------------------------
    # 低レベルリクエスト
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        auth: bool = True,
    ) -> Any:
        params = params or {}
        query_string = ""
        if params:
            query_string = "?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)

        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        url = BASE_URL + path + query_string

        if auth:
            if not (self.api_key and self.api_secret and self.passphrase):
                raise RuntimeError(
                    "認証が必要なエンドポイントですが、APIキー/シークレット/パスフレーズが設定されていません。"
                )
            timestamp = self._timestamp_ms()
            sign_path = path + query_string
            sign = self._sign(timestamp, method, sign_path, body_str)
            headers = self._auth_headers(timestamp, sign)
        else:
            headers = {"Content-Type": "application/json"}

        resp = self.session.request(
            method, url, headers=headers, data=body_str if body else None, timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "00000":
            raise BitgetAPIError(data.get("code", "?"), data.get("msg", "unknown error"), raw=data)
        return data.get("data")

    # ------------------------------------------------------------------
    # 公開: マーケットデータ
    # ------------------------------------------------------------------

    def get_futures_ticker(self, symbol: str, product_type: str = "USDT-FUTURES") -> dict:
        data = self._request(
            "GET", "/api/v2/mix/market/ticker",
            params={"symbol": symbol, "productType": product_type}, auth=False,
        )
        if not data:
            raise RuntimeError(f"{symbol} のティッカーが取得できませんでした")
        return data[0]

    def get_contract_config(self, symbol: str, product_type: str = "USDT-FUTURES") -> dict:
        """最小注文数量・価格精度などの銘柄仕様を取得(実運用前の確認に使用)。"""
        data = self._request(
            "GET", "/api/v2/mix/market/contracts",
            params={"symbol": symbol, "productType": product_type}, auth=False,
        )
        if not data:
            raise RuntimeError(f"{symbol} のコントラクト仕様が取得できませんでした")
        return data[0]

    # ------------------------------------------------------------------
    # 認証: 口座 / ポジション
    # ------------------------------------------------------------------

    def get_futures_account(self, symbol: str, product_type: str = "USDT-FUTURES", margin_coin: str = "USDT") -> dict:
        return self._request(
            "GET", "/api/v2/mix/account/account",
            params={"symbol": symbol, "productType": product_type, "marginCoin": margin_coin},
        )

    def get_single_position(
        self, symbol: str, product_type: str = "USDT-FUTURES", margin_coin: str = "USDT"
    ) -> list:
        """保有ポジション一覧を返す(ポジションがなければ空リスト)。"""
        data = self._request(
            "GET", "/api/v2/mix/position/single-position",
            params={"symbol": symbol, "productType": product_type, "marginCoin": margin_coin},
        )
        return data or []

    def set_leverage(
        self, symbol: str, leverage: str, product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT", hold_side: Optional[str] = None,
    ) -> dict:
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "leverage": leverage,
        }
        if hold_side:
            body["holdSide"] = hold_side  # hedgeモードでlong/short別レバレッジを使う場合のみ
        return self._request("POST", "/api/v2/mix/account/set-leverage", body=body)

    def set_margin_mode(
        self, symbol: str, margin_mode: str, product_type: str = "USDT-FUTURES", margin_coin: str = "USDT"
    ) -> dict:
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "marginMode": margin_mode,  # "isolated" or "crossed"
        }
        return self._request("POST", "/api/v2/mix/account/set-margin-mode", body=body)

    # ------------------------------------------------------------------
    # 認証: 発注
    # ------------------------------------------------------------------

    def place_futures_order(
        self,
        symbol: str,
        side: str,                      # "buy" (ロング) / "sell" (ショート)
        size: str,                      # base資産(XAU/XAUT等)の数量
        order_type: str = "market",
        product_type: str = "USDT-FUTURES",
        margin_mode: str = "isolated",
        margin_coin: str = "USDT",
        price: Optional[str] = None,
        force: str = "gtc",
        reduce_only: bool = False,
        client_oid: Optional[str] = None,
    ) -> dict:
        """
        one-wayモード前提の発注ラッパー。
        - 新規建て: side="buy"(ロング) / side="sell"(ショート), reduce_only=False
        - 決済:     ロング決済は side="sell", ショート決済は side="buy", reduce_only=True
        """
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": size,
            "side": side,
            "orderType": order_type,
            "force": force,
            "reduceOnly": "YES" if reduce_only else "NO",
            "clientOid": client_oid or self.new_client_oid(),
        }
        if price is not None:
            body["price"] = price
        return self._request("POST", "/api/v2/mix/order/place-order", body=body)

    def get_futures_order_info(
        self, symbol: str, order_id: Optional[str] = None, client_oid: Optional[str] = None,
        product_type: str = "USDT-FUTURES",
    ) -> dict:
        params = {"symbol": symbol, "productType": product_type}
        if order_id:
            params["orderId"] = order_id
        if client_oid:
            params["clientOid"] = client_oid
        data = self._request("GET", "/api/v2/mix/order/detail", params=params)
        if not data:
            raise RuntimeError("注文情報が取得できませんでした")
        return data
