"""
QA-Board's OAuth 2.1 authorization server (backend/oauth.py): what it checks, without a database.
The flow, on PostgreSQL: integration_tests/test_oauth.py.
"""
import base64
import hashlib
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from backend import oauth


REDIRECT_URI = "https://openwebui.example.com/oauth/clients/mcp:qaboard/callback"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest()).rstrip(b'=').decode()


@pytest.fixture
def client(monkeypatch):
  registered = SimpleNamespace(id=1, client_id="qab_app_x", name="Open WebUI", redirect_uris=[REDIRECT_URI])
  monkeypatch.setattr(oauth, 'find_client', lambda client_id: registered if client_id == registered.client_id else None)
  return registered


def params(**changes):
  values = {"client_id": "qab_app_x", "redirect_uri": REDIRECT_URI, "response_type": "code", "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", "state": "xyz", **changes}
  return MultiDict([(k, v) for k, v in values.items() if v is not None])


def test_metadata():
  metadata = oauth.metadata("https://qa.example.com")
  assert metadata["issuer"] == "https://qa.example.com"
  assert metadata["token_endpoint"] == "https://qa.example.com/api/v1/oauth/token"
  assert metadata["code_challenge_methods_supported"] == ["S256"]
  assert metadata["authorization_response_iss_parameter_supported"] is True


def test_pkce():
  assert oauth.pkce_matches("v" * 43, CHALLENGE)
  assert not oauth.pkce_matches("w" * 43, CHALLENGE)
  # too short, or not a string
  assert not oauth.pkce_matches("v" * 42, CHALLENGE)
  assert not oauth.pkce_matches(None, CHALLENGE)


@pytest.mark.parametrize("uri, ok", [
  (REDIRECT_URI, True),
  ("http://localhost:8080/oauth/callback", True),
  ("javascript:alert(1)", False),
  ("https://a.example.com/cb#fragment", False),
  ("https://user:password@a.example.com/cb", False),
  ("/relative", False),
])
def test_redirect_uris(uri, ok):
  assert (oauth.redirect_uri_problem(uri) is None) == ok


def test_clean_redirect_uris():
  assert oauth.clean_redirect_uris([f" {REDIRECT_URI} ", REDIRECT_URI, ""]) == [REDIRECT_URI]
  for wrong in (None, [], [""], [f"https://a.example.com/{i}" for i in range(11)]):
    with pytest.raises(ValueError):
      oauth.clean_redirect_uris(wrong)


def test_redirect_url_keeps_the_registered_query():
  assert oauth.redirect_url("https://a.example.com/cb", code="c d", state=None) == "https://a.example.com/cb?code=c+d"
  assert oauth.redirect_url("https://a.example.com/cb?x=%20", code="c") == "https://a.example.com/cb?x=%20&code=c"


def test_scopes():
  assert oauth.parse_scope(None) == ["read"]
  # offline_access and the like are ignored
  assert oauth.parse_scope("read offline_access") == ["read"]
  with pytest.raises(oauth.OAuthError) as e:
    oauth.parse_scope("admin")
  assert e.value.error == "invalid_scope"


def test_resources():
  assert oauth.clean_resource([]) is None
  assert oauth.clean_resource(["https://qa.example.com/mcp"]) == "https://qa.example.com/mcp"
  for wrong in (["mcp"], ["https://qa.example.com/mcp#x"], ["https://a.example.com", "https://b.example.com"]):
    with pytest.raises(oauth.OAuthError) as e:
      oauth.clean_resource(wrong)
    assert e.value.error == "invalid_target"


def basic(client_id, secret):
  return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


def test_client_credentials():
  assert oauth.client_credentials(None, MultiDict({"client_id": "a", "client_secret": "b"})) == ("a", "b", False)
  assert oauth.client_credentials(basic("a%3Ab", "c"), MultiDict()) == ("a:b", "c", True)
  # with its id in the form too, the same
  assert oauth.client_credentials(basic("a", "c"), MultiDict({"client_id": "a"})) == ("a", "c", True)
  for authorization, form, error in [
    ("Basic not-base64!", {}, "invalid_client"),
    ("Basic " + base64.b64encode(b"no-secret").decode(), {}, "invalid_client"),
    (basic("a", "c"), {"client_id": "other"}, "invalid_client"),
    (basic("a", "c"), {"client_secret": "c"}, "invalid_request"),
  ]:
    with pytest.raises(oauth.OAuthError) as e:
      oauth.client_credentials(authorization, MultiDict(form))
    assert e.value.error == error, authorization
    assert e.value.basic == (error == "invalid_client")


def test_authorization_requests(client):
  req = oauth.authorization_request(params(scope="read", resource="https://qa.example.com/mcp"))
  assert (req.client, req.redirect_uri, req.scopes, req.state, req.resource) == (client, REDIRECT_URI, ["read"], "xyz", "https://qa.example.com/mcp")
  assert oauth.serialize_request(req)["redirect_host"] == "openwebui.example.com"


@pytest.mark.parametrize("changes", [{"client_id": "qab_app_nope"}, {"client_id": None}, {"redirect_uri": "https://evil.example.com/cb"}, {"redirect_uri": None}])
def test_unknown_apps_and_redirect_uris_are_not_redirected(client, changes):
  with pytest.raises(oauth.ClientError):
    oauth.authorization_request(params(**changes))


@pytest.mark.parametrize("changes, error", [
  ({"response_type": "token"}, "unsupported_response_type"),
  ({"code_challenge": None}, "invalid_request"),
  ({"code_challenge_method": "plain"}, "invalid_request"),
  ({"code_challenge_method": None}, "invalid_request"),
  ({"code_challenge": "short"}, "invalid_request"),
  ({"scope": "write"}, "invalid_scope"),
  ({"resource": "not-a-uri"}, "invalid_target"),
])
def test_other_errors_go_back_to_the_app(client, changes, error):
  with pytest.raises(oauth.RedirectError) as e:
    oauth.authorization_request(params(**changes))
  assert e.value.error == error
  url = e.value.url("https://qa.example.com")
  assert url.startswith(REDIRECT_URI + "?error=" + error) and "state=xyz" in url and "iss=https%3A%2F%2Fqa.example.com" in url


def test_parameters_are_sent_once(client):
  twice = params()
  twice.add("state", "abc")
  with pytest.raises(oauth.RedirectError) as e:
    oauth.authorization_request(twice)
  assert e.value.error == "invalid_request"
  twice = params()
  twice.add("redirect_uri", REDIRECT_URI)
  with pytest.raises(oauth.ClientError):
    oauth.authorization_request(twice)
