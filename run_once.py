"""
run_once.py
-----------
cron(ラッコサーバー等)から定期的に(推奨: 1分間隔)起動されるエントリーポイント。
1回だけ価格チェック・売買判定を行い、状態をJSONファイルに保存して終了する。

cron設定例(cPanel「Cronジョブ」):
  分: */1   時: *   日: *   月: *   曜日: *
  コマンド:
    /home/ユーザー名/bitget_arb_bot/venv/bin/python \
      /home/ユーザー名/bitget_arb_bot/run_once.py \
      >> /home/ユーザー名/bitget_arb_bot/cron_stdout.log 2>&1

前回の実行がまだ終わっていない場合(ネットワーク遅延等)に二重発注しないよう、
ロックファイルによる排他制御を行う。ロックが取得できない場合は何もせず終了する。
"""

from __future__ import annotations

import errno
import logging
import logging.handlers
import os
import sys
import time

from config import Config
from strategy import ArbitrageBot


def setup_logging(cfg: Config) -> None:
    logger = logging.getLogger("arb_bot")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        cfg.app_log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)


class LockError(Exception):
    pass


class FileLock:
    """fcntl.flock を使った単純な排他ロック(Linux/Unix専用)。"""

    def __init__(self, path: str):
        self.path = path
        self._fd = None

    def __enter__(self) -> "FileLock":
        import fcntl  # Unix専用。ラッコサーバー等のLinux共用サーバーを前提とする。

        self._fd = open(self.path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self._fd.close()
            self._fd = None
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise LockError("前回の実行がまだ終わっていないため、今回はスキップします。") from e
            raise
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()


def main() -> int:
    cfg = Config()
    setup_logging(cfg)
    logger = logging.getLogger("arb_bot")

    start = time.time()
    try:
        with FileLock(cfg.lock_file_path):
            bot = ArbitrageBot(cfg)
            try:
                bot.step()
            finally:
                bot.save_state()
                bot.close()
    except LockError as e:
        logger.warning(str(e))
        return 0
    except Exception:
        logger.exception("実行中に予期しないエラーが発生しました")
        return 1
    finally:
        elapsed = time.time() - start
        logger = logging.getLogger("arb_bot")
        if elapsed > cfg.max_run_seconds:
            logger.warning(
                "1回の実行に%.1f秒かかりました(想定上限%.1f秒)。"
                "cronの実行間隔(1分)より長くなっていないか確認してください。",
                elapsed, cfg.max_run_seconds,
            )
        else:
            logger.info("実行完了(%.1f秒)", elapsed)

    return 0


if __name__ == "__main__":
    sys.exit(main())
