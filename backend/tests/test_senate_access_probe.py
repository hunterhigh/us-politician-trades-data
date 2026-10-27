"""Temporary read-only hosted-runner probe; do not merge into production."""
import hashlib
import html
from html.parser import HTMLParser
import http.cookiejar
import json
import os
import re
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class PageSignals(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf_inputs = 0
        self.scripts = 0
        self.external_scripts = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'input' and values.get('name') == 'csrfmiddlewaretoken':
            self.csrf_inputs += 1
        if tag == 'script':
            self.scripts += 1
            host = urlsplit(values.get('src', '')).hostname
            self.external_scripts += int(bool(host and host != 'efdsearch.senate.gov'))


@unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true' and
                     os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch',
                     'Only an explicitly dispatched diagnostic branch may make this public GET')
class HostedSenateAccessProbe(unittest.TestCase):
    def test_single_public_home_get_without_agreement_or_catalog_requests(self):
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
        request = urllib.request.Request('https://efdsearch.senate.gov/search/home/', headers={
            'Accept': 'text/html,application/xhtml+xml',
            'User-Agent': 'unison-senate-evidence/0.1 (+https://github.com/hunterhigh/us-politician-trades-data)',
        }, method='GET')
        result = {'method': 'GET', 'path': '/search/home/', 'agreement_submitted': False}
        try:
            try:
                response = opener.open(request, timeout=25)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                content = response.read(1024 * 1024)
                result.update(status=response.code, server=response.headers.get('Server'),
                              final_path=urlsplit(response.geturl()).path,
                              content_type=response.headers.get('Content-Type'))
            text = content.decode('utf-8', errors='replace')
            parser = PageSignals()
            parser.feed(text)
            result.update(csrf_input_count=parser.csrf_inputs, script_count=parser.scripts,
                          external_script_count=parser.external_scripts,
                          javascript_required_text=bool(re.search(
                              r'enable javascript|javascript is required', text, re.I)),
                          akamai_markers=[name for name in ('_abck', 'bm_sz', 'ak_bmsc', 'bm_sv',
                                                            'akamai', 'botmanager')
                                          if name in text.lower()],
                          cookie_metadata=[{'name': cookie.name, 'domain': cookie.domain,
                                            'path': cookie.path, 'secure': cookie.secure}
                                           for cookie in cookies])
            # Support reference numbers are public error identifiers, not credentials.
            reference = re.search(r'Reference\s*#([0-9a-fA-F.\-]+)', html.unescape(text))
            if reference:
                result['edge_reference'] = reference.group(1)
        except (urllib.error.URLError, TimeoutError) as error:
            result['transport_error_type'] = type(error).__name__
        print('SENATE_READ_ONLY_ACCESS_DIAGNOSTIC=' + json.dumps(result, sort_keys=True), flush=True)
