"""Retry only transient read failures, never invalid response content."""
import http.client
import time
from urllib.error import HTTPError, URLError


TRANSPORT_ERRORS = (HTTPError, URLError, TimeoutError, ConnectionError, http.client.HTTPException)


def read_with_retry(operation, *, sleeper=None):
    sleeper = sleeper or time.sleep
    for attempt in range(3):
        try:
            return operation()
        except TRANSPORT_ERRORS as error:
            transient = not isinstance(error, HTTPError) or error.code in {408, 425, 429, 500, 502, 503, 504}
            if not transient or attempt == 2:
                raise
            sleeper(2 ** attempt)


def read_response(request, opener, *, timeout, maximum, expected_url, unexpected):
    """Read a bounded exact-destination response; callers own retries and source checks."""
    with opener(request, timeout=timeout) as response:
        if response.status != 200 or response.geturl() != expected_url:
            raise unexpected
        content = response.read(maximum + 1)
        headers = {name.lower(): value for name, value in response.headers.items()
                   if name.lower() in {"etag", "last-modified", "content-type"}}
    return content, headers
