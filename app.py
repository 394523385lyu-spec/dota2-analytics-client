import faulthandler
import logging
import sys
from pathlib import Path

from dota_client.ui.main_window import run
from dota_client.paths import LOG_DIR


LOG_PATH = LOG_DIR / "client.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_fault_log = LOG_PATH.open("a", encoding="utf-8")
faulthandler.enable(_fault_log, all_threads=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _exception_hook(exc_type, exc_value, traceback) -> None:
    logging.exception(
        "未处理异常",
        exc_info=(exc_type, exc_value, traceback),
    )
    sys.__excepthook__(exc_type, exc_value, traceback)


sys.excepthook = _exception_hook


if __name__ == "__main__":
    run()
