from __future__ import annotations

import uuid

from phase31_complete import Phase31Application
from phase32_product_spec import gaps, manifest
from platform_wsgi import HTTPError, RID_RE


class Phase32Application(Phase31Application):
    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if path == "/.well-known/aurora-product.json" and method == "GET":
            return self._json_document(environ, start_response, manifest(), rid)

        if path == "/api/public/product/capabilities" and method == "GET":
            return self._response(environ, start_response, 200, manifest(), rid)

        if path == "/api/public/product/gaps" and method == "GET":
            try:
                query = self._query(environ)
                priority = self._value(query, "priority", "")
                return self._response(environ, start_response, 200, gaps(priority), rid)
            except ValueError as exc:
                return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))

        return super().__call__(environ, start_response)


application = Phase32Application()
