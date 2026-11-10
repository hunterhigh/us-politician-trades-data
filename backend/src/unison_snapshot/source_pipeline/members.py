"""Bounded official roster transport and XML framing shared by source adapters."""
import re
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from ..source_transport import read_response


def download_roster(url, headers, *, timeout, limit, error, label,
                    retry_delays=(), opener=None, sleeper=None):
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = opener or urllib.request.urlopen
    sleeper = sleeper or time.sleep
    attempts = len(retry_delays) + 1
    for attempt in range(attempts):
        try:
            content, selected = read_response(request, opener, timeout=timeout,
                maximum=limit, expected_url=url,
                unexpected=error(f"{label} returned an unexpected response"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {403, 429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise error(f"{label} returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise error(f"{label} is unavailable") from None
        sleeper(retry_delays[attempt])
    if len(content) > limit:
        raise error(f"{label} exceeds its size limit")
    return content, selected


def roster_xml(content, *, limit, error, source, require_bytes=False):
    if ((require_bytes and not isinstance(content, bytes)) or len(content) > limit or
            b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper()):
        raise error(f"Invalid {source} member roster")
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        raise error(f"{source} member roster XML is invalid") from None


def ascii_name(value):
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def ascii_words(value):
    return re.findall(r"[a-z]+", ascii_name(value))
