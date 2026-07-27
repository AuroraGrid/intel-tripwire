from __future__ import annotations

import uuid

from phase28_complete import Phase28Application
from phase29_enterprise import EnterpriseControlPlane
from platform_wsgi import HTTPError, RID_RE


class Phase29Application(Phase28Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enterprise = EnterpriseControlPlane(self.store)

    def _enterprise_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/enterprise/policies" and method == "GET":
            return 200, {"policies": self.enterprise.policies(actor, value("policy_key"), int(value("limit", "100")))}
        if path == "/api/platform/enterprise/policies" and method == "POST":
            return 201, self.enterprise.publish_policy(actor, body())
        if path == "/api/platform/enterprise/deployments" and method == "GET":
            return 200, {"deployments": self.enterprise.deployments(actor, value("environment"), int(value("limit", "100")))}
        if path == "/api/platform/enterprise/deployments" and method == "POST":
            return 201, self.enterprise.register_deployment(actor, body())
        if path == "/api/platform/enterprise/attestations" and method == "GET":
            return 200, {"attestations": self.enterprise.attestations(actor, value("deployment_id"), int(value("limit", "100")))}
        if path == "/api/platform/enterprise/attestations" and method == "POST":
            return 201, self.enterprise.record_attestation(actor, body())
        if path == "/api/platform/enterprise/compliance" and method == "GET":
            return 200, self.enterprise.compliance(actor, value("deployment_id"))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if path == "/.well-known/aurora-enterprise.json" and method == "GET":
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 29,
                    "purpose": "enterprise deployment governance and compliance",
                    "controls": {
                        "workspace_scoped": True,
                        "policies_are_append_only_versions": True,
                        "attestations_expire": True,
                        "external_ai_required": False,
                        "compliance_is_not_self_certified": True,
                    },
                },
                rid,
            )

        namespace = path == "/api/platform/enterprise" or path.startswith("/api/platform/enterprise/")
        if not namespace:
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._enterprise_response(path, method, actor, environ)
            return self._response(environ, start_response, status, payload, rid)
        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except ValueError as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except Exception:
            return self._error(environ, start_response, rid, HTTPError(500, "internal_error", "internal server error"))


application = Phase29Application()
