"""チェック失敗の分類。docs/log-spec.md 5.1節のエラーコードと対応する。"""

CONFIG_INVALID = "CONFIG_INVALID"
DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
DB_QUERY_FAILED = "DB_QUERY_FAILED"
UNKNOWN = "UNKNOWN"

# error_message はログ1行に載るため、長すぎる例外メッセージは切り詰める
MAX_ERROR_MESSAGE_LENGTH = 1000


class CheckError(Exception):
    """error_code を伴うチェック失敗。分類できない例外は main 側で UNKNOWN として扱う。"""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class ConfigError(CheckError):
    def __init__(self, message: str):
        super().__init__(CONFIG_INVALID, message)


def classify(exc: BaseException) -> tuple[str, str]:
    """例外を (error_code, error_message) に変換する。"""
    if isinstance(exc, CheckError):
        code, message = exc.error_code, str(exc)
    else:
        code, message = UNKNOWN, f"{type(exc).__name__}: {exc}"
    message = message.strip() or type(exc).__name__
    return code, message[:MAX_ERROR_MESSAGE_LENGTH]
