"""
Where the wizard connects: the QA-Board server, and optionally an LLM behind an OpenAI-compatible API.

Settings are read like other qaboard settings: environment > ~/.qaboard/secrets.yaml > site package > defaults.
Sites put their own defaults in their site package, so users only have to say yes. For instance an internal LLM gateway:
    "QABOARD_LLM_BASE_URL": "https://llm-gateway.example.com/v1",
    "QABOARD_LLM_MODEL": "qwen3-coder",
    "QABOARD_LLM_KEY_URL": "https://wiki.example.com/how-to-get-an-llm-key",   # shown when asking for a key
    "QABOARD_LLM_VERIFY": "false",   # or the path to a CA bundle, like QABOARD_API_VERIFY
"""
import os
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import yaml

from ..site_config import site_config, user_secret, as_requests_verify, get_qaboard_url


OPENAI_BASE_URL = 'https://api.openai.com/v1'
TIMEOUT = 10


def user_settings_path() -> Path:
  return Path(os.getenv('QA_USER_SECRETS', '~/.qaboard/secrets.yaml')).expanduser()


def save_user_settings(values: Dict[str, str]) -> Path:
  """
  Adds settings to ~/.qaboard/secrets.yaml, readable only by the user.
  Other settings in the file are kept, but not its comments.
  """
  path = user_settings_path()
  current: Dict[str, Any] = {}
  if path.exists():
    current = yaml.safe_load(path.read_text()) or {}
    if not isinstance(current, dict):
      raise ValueError(f"{path} should contain a mapping of settings, please fix it first.")
  current.update(values)
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  # A new file, readable only by the user from the start (mkstemp: 0600, O_EXCL, doesn't follow symlinks)
  fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
  try:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
      f.write("# Personal QA-Board settings and secrets, see https://samsung.github.io/qaboard/docs/installation\n")
      yaml.safe_dump(current, f, default_flow_style=False, sort_keys=True)
  except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise
  os.replace(tmp, path)
  return path


def requests_session(verify: Union[bool, str]):
  import requests
  session = requests.Session()
  session.verify = verify
  if verify is False:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
  return session


@dataclass
class Server:
  url: str
  is_default: bool
  token: Optional[str]
  verify: Union[bool, str]
  api_prefix: Optional[str] = None   # QABOARD_API_PREFIX, for the configured URL only

  @property
  def is_insecure(self) -> bool:
    return is_insecure(self.url)

  @property
  def api(self) -> str:
    return self.api_prefix.rstrip('/') if self.api_prefix else f"{self.url.rstrip('/')}/api/v1"

  def use_url(self, url: str):
    self.url, self.is_default, self.api_prefix = url, False, None

  @staticmethod
  def from_settings() -> 'Server':
    url, is_default = get_qaboard_url()
    return Server(
      url=url, is_default=is_default, token=user_secret('QA_TOKEN'),
      verify=as_requests_verify(site_config('QABOARD_API_VERIFY')), api_prefix=site_config('QABOARD_API_PREFIX'),
    )

  def probe(self) -> Tuple[bool, str, Dict[str, Any]]:
    """Whether the server answers, a message for the user, and its public configuration."""
    session = requests_session(self.verify)
    try:
      r = session.get(f"{self.api}/health", timeout=TIMEOUT)
    except Exception as e:
      return False, short_error(e), {}
    if r.status_code != 200:
      return False, f"HTTP {r.status_code} from {self.api}/health", {}
    try:
      config = session.get(f"{self.api}/config", timeout=TIMEOUT).json()
    except Exception:
      config = {}
    return True, "online", config if isinstance(config, dict) else {}

  def probe_typed(self, typed: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Probes what the user typed, adding https:// or http:// if needed. Uses the first URL that works."""
    result: Tuple[bool, str, Dict[str, Any]] = (False, 'invalid URL', {})
    for url in with_scheme(typed):
      self.use_url(url)
      result = self.probe()
      if result[0]:
        return result
    self.use_url(with_scheme(typed)[0])
    return result

  def whoami(self, token: str) -> Optional[str]:
    """The user name a token belongs to, None if it's not valid."""
    session = requests_session(self.verify)
    try:
      info = session.get(f"{self.api}/user/me/", headers={'Authorization': f'Bearer {token}'}, timeout=TIMEOUT).json()
    except Exception:
      return None
    return info.get('user_name') if info.get('is_authenticated') else None

  def login_for_token(self, username: str, password: str) -> str:
    """Logs in, and creates a personal API token. The password is used once, never saved."""
    session = requests_session(self.verify)
    # Never resend the password to wherever a redirect points
    r = session.post(f"{self.api}/user/auth/", data={'username': username, 'password': password}, timeout=TIMEOUT, allow_redirects=False)
    if r.status_code != 200:
      try:
        error = r.json().get('error')
      except Exception:
        error = None
      raise RuntimeError(error or f"login failed (HTTP {r.status_code})")
    r = session.post(f"{self.api}/user/token/", timeout=TIMEOUT)
    if r.status_code != 200:
      raise RuntimeError(f"could not create a token (HTTP {r.status_code})")
    return r.json()['token']


@dataclass
class LLM:
  base_url: str
  api_key: Optional[str]
  model: Optional[str]
  verify: Union[bool, str]
  key_url: Optional[str] = None   # where users get a key, set by sites

  @property
  def host(self) -> str:
    return urlparse(self.base_url).netloc or self.base_url

  @property
  def is_openai(self) -> bool:
    return self.base_url.startswith('https://api.openai.com/')

  @property
  def is_local(self) -> bool:
    return is_local(self.base_url)

  @property
  def is_insecure(self) -> bool:
    """Keys and code would be sent unencrypted over the network."""
    return is_insecure(self.base_url)

  @staticmethod
  def from_settings(model: Optional[str] = None) -> 'LLM':
    base_url = site_config('QABOARD_LLM_BASE_URL') or os.getenv('OPENAI_BASE_URL') or OPENAI_BASE_URL
    llm = LLM(
      base_url=base_url.rstrip('/'),
      api_key=site_config('QABOARD_LLM_API_KEY'),
      model=model or site_config('QABOARD_LLM_MODEL'),
      verify=as_requests_verify(site_config('QABOARD_LLM_VERIFY')),
      key_url=site_config('QABOARD_LLM_KEY_URL'),
    )
    # OPENAI_API_KEY is only sent where it's meant to go, never to another provider or a site's gateway
    if not llm.api_key and (llm.is_openai or base_url == os.getenv('OPENAI_BASE_URL')):
      llm.api_key = os.getenv('OPENAI_API_KEY')
    return llm

  def list_models(self, base_url: Optional[str] = None) -> Tuple[Optional[List[str]], str, Optional[int]]:
    """The models the API offers (or None), a message, and the HTTP status. Also checks the API key."""
    base_url = base_url or self.base_url
    session = requests_session(self.verify)
    headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
    try:
      r = session.get(f"{base_url}/models", headers=headers, timeout=TIMEOUT)
    except Exception as e:
      return None, short_error(e), None
    if r.status_code in (401, 403):
      return None, "the API key was rejected", r.status_code
    if r.status_code != 200:
      return None, f"HTTP {r.status_code} from {base_url}/models", r.status_code
    try:
      return sorted(m['id'] for m in r.json()['data']), "ok", 200
    except Exception:
      return None, f"unexpected answer from {base_url}/models", r.status_code


def with_scheme(url: str) -> List[str]:
  """URLs to try for what the user typed: qaboard-srv:5151 may be https or http."""
  url = url.strip().rstrip('/')
  if '://' in url:
    return [url]
  return [f'https://{url}', f'http://{url}']


def shadowing_settings(keys: List[str]) -> Dict[str, List[str]]:
  """For settings we save in ~/.qaboard/secrets.yaml, the settings that would win over them."""
  shadowed = {}
  for key in keys:
    # Environment variables win over the secrets file (see site_config)
    shadows = [key] if os.environ.get(key) else []
    if key == 'QABOARD_URL':
      # see get_qaboard_url
      if site_config('QABOARD_HOSTNAME') and site_config('QABOARD_PORT'):
        shadows += ['QABOARD_HOSTNAME', 'QABOARD_PORT']
      elif site_config('QABOARD_HOST'):
        shadows.append('QABOARD_HOST')
      if site_config('QABOARD_API_PREFIX'):
        shadows.append('QABOARD_API_PREFIX')
    if shadows:
      shadowed[key] = shadows
  return shadowed


def is_local(url: str) -> bool:
  return urlparse(url).hostname in ('localhost', '127.0.0.1', '::1')


def is_insecure(url: str) -> bool:
  return urlparse(url).scheme == 'http' and not is_local(url)


def short_error(e: Exception) -> str:
  """Connection errors from requests are very verbose."""
  import requests
  if isinstance(e, requests.exceptions.SSLError):
    return "TLS certificate check failed (internal CA? set QABOARD_API_VERIFY / QABOARD_LLM_VERIFY to a CA bundle, or false)"
  if isinstance(e, requests.exceptions.ConnectTimeout):
    return "connection timed out"
  if isinstance(e, requests.exceptions.ConnectionError):
    return "could not connect"
  return str(e).splitlines()[0][:200]


def chat_models(models: List[str]) -> List[str]:
  """Models that can chat, without embeddings, speech, images..."""
  excluded = ('embed', 'whisper', 'tts', 'dall-e', 'image', 'audio', 'moderation', 'realtime', 'transcribe', 'search', 'babbage', 'davinci', 'rerank')
  return [m for m in models if not any(x in m.lower() for x in excluded)]


def suggest_model(models: List[str]) -> Optional[str]:
  """A reasonable default among available models: a capable, general-purpose coding model."""
  preferences = ('coder', 'claude', 'gpt-5', 'gpt-4.1', 'gpt-4o', 'qwen', 'deepseek', 'llama', 'mistral', 'gemini')
  chat_models_ = chat_models(models)
  for preference in preferences:
    matches = [m for m in chat_models_ if preference in m.lower()]
    if matches:
      # the shortest name is usually the main model of its family, not a dated snapshot or a variant
      return sorted(matches, key=lambda m: (len(m), m))[0]
  return chat_models_[0] if chat_models_ else None
