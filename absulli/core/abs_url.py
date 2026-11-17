from urllib.parse import urlsplit


def validate_abs_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("Audiobookshelf URL must start with http:// or https://.")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("Audiobookshelf URL contains invalid characters.")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Audiobookshelf URL has an invalid hostname or port.") from exc
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Audiobookshelf URL must have a hostname and no embedded credentials.")
    if parsed.query or parsed.fragment or "?" in url or "#" in url:
        raise ValueError("Audiobookshelf URL cannot contain a query or fragment.")
    if port is not None and port == 0:
        raise ValueError("Audiobookshelf URL has an invalid port.")
    if "\\" in url or "%" in parsed.netloc:
        raise ValueError("Audiobookshelf URL contains invalid characters.")
    return url
