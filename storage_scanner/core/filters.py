from decimal import Decimal, InvalidOperation

from storage_scanner.platform_fs.paths import contains_path, path_key, entry_path_key


def mib_to_bytes(value: str) -> int:
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise ValueError
        numerator, denominator = number.as_integer_ratio()
        whole, fraction = divmod(numerator * (1024 * 1024), denominator)
        if fraction:
            raise ValueError
        return whole
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("Enter a nonnegative MiB value representing whole bytes") from exc


def bytes_to_mib(value: int) -> str:
    whole, remainder = divmod(value, 1024 * 1024)
    fraction = str(remainder * 5**20).zfill(20).rstrip("0")
    return str(whole) + ("." + fraction if fraction else "")


def matches_size(size_bytes: int, minimum: int) -> bool:
    return size_bytes >= minimum


# Compatibility import for the v1.1 public API.
from storage_scanner.platform_fs.filters import PathFilter
