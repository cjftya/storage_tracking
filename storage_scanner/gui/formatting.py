"""Small presentation helpers shared by the independent result trees."""
ROW_COLORS = ("#f5f5f5", "#e8e8e8")
ROW_TAGS = ("row_light", "row_dark")


def display_size(size):
    if size < 1024:
        return f"{size:,} B"
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        size /= 1024
        if size < 1024 or unit == "PiB":
            return f"{size:,.2f} {unit}"
