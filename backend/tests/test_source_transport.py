import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_reports import OgePdfClient


class SourceTransportTests(unittest.TestCase):
    def opener(self,status,body=b'%PDF-1.7\n%%EOF'):
        class Response:
            status=200
            headers={'Content-Type':'application/pdf'}
            def __init__(self,url):self.url=url
            def geturl(self):return self.url
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return body
        class Opener:
            calls=0
            def open(self,request,**kwargs):
                self.calls+=1
                if self.calls<3 and status:
                    raise HTTPError(request.full_url,status,'bounded test',{},None)
                return Response(request.full_url)
        return Opener()

    def test_actual_pdf_client_retries_only_transient_transport_before_content_validation(self):
        opener=self.opener(503)
        with patch('unison_snapshot.source_transport.time.sleep') as sleep:
            OgePdfClient(opener=opener).download('https://example.invalid/report.pdf')
        self.assertEqual(opener.calls,3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[1,2])
        for opener in [self.opener(403),self.opener(None,b'not a pdf')]:
            with patch('unison_snapshot.source_transport.time.sleep') as sleep:
                with self.assertRaises(OgeCatalogError):
                    OgePdfClient(opener=opener).download('https://example.invalid/report.pdf')
            self.assertEqual(opener.calls,1)
            sleep.assert_not_called()


if __name__=='__main__':unittest.main()
