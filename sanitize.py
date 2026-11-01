"""
sanitize.py — make the review safe to post as a public GitHub comment.

The model reads attacker-influenceable text (fork PR logs, test output, diffs).
Even with the agent's security rules, assume an injected instruction might get
through, and make sure the posted comment can't be used to:

- ping people or teams          -> @mentions are neutralised
- exfiltrate data via a request -> images and raw HTML are removed
- phish                         -> links to hosts outside the allowlist are defanged
- hide content from reviewers   -> HTML comments are removed
- trigger bots                  -> lines starting with a /command are neutralised
- leak a secret                 -> the whole text is redacted again

Code blocks are left as they are (GitHub does not render links, images or
mentions inside them), so suggested fixes and diffs come through intact.
"""

import os
import re
from urllib.parse import urlsplit

from tools import redact_secrets

ZWSP = "​"   # zero-width space: breaks a mention or command without visible change
MAX_COMMENT_CHARS = 60000   # GitHub's limit is 65,536
DEFAULT_ALLOWED_HOSTS = ("github.com",)

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_IMAGE = re.compile(r"!\[[^\]\n]{0,300}\]\([^)\n]{0,2000}\)")
_REF_DEF = re.compile(r"^\s{0,3}\[[^\]\n]{1,300}\]:\s*(\S+).*$", re.M)
_MD_LINK = re.compile(r"\[([^\]\n]{0,300})\]\(\s*<?([^)\s>]{1,2000})>?(?:\s+\"[^\"\n]{0,300}\")?\s*\)")
_BARE_URL = re.compile(r"\b(?:https?|ftp)://[^\s<>()\[\]`'\"]{1,2000}", re.I)
_WWW = re.compile(r"(?<![\w./@-])www\.[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+[^\s<>()\[\]`'\"]{0,2000}", re.I)
_HTML_TAG = re.compile(r"<(?=[A-Za-z/!?])")
_MENTION = re.compile(r"(?<![\w`/.@-])@(?=[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[A-Za-z0-9._-]{1,100})?\b)")
_SLASH_CMD = re.compile(r"^(\s*(?:>\s*)*)/(?=[A-Za-z])", re.M)


def _allowed_hosts() -> tuple:
    extra = os.environ.get("REVIEW_ALLOWED_LINK_HOSTS", "")
    return DEFAULT_ALLOWED_HOSTS + tuple(h.strip().lower() for h in extra.split(",") if h.strip())


def _host_allowed(url: str, allowed: tuple) -> bool:
    try:
        parts = urlsplit(url if "://" in url else "https://" + url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in allowed)


def _defang(url: str) -> str:
    """hxxps://evil[.]com/x — readable, never clickable or autolinked."""
    out = re.sub(r"^http", "hxxp", url, flags=re.I)
    out = re.sub(r"^ftp", "fxp", out, flags=re.I)
    scheme, sep, rest = out.partition("://")
    if not sep:
        scheme, rest = "", out
    host, slash, path = rest.partition("/")
    host = host.replace(".", "[.]")
    return (scheme + sep if sep else "") + host + slash + path


def _sanitize_prose(text: str, allowed: tuple) -> str:
    text = _HTML_COMMENT.sub("", text)
    text = _IMAGE.sub("[image removed]", text)
    text = _REF_DEF.sub(lambda m: m.group(0) if _host_allowed(m.group(1), allowed) else "", text)

    links = []   # protect allowed links from the bare-URL pass below

    def md_link(m):
        label, url = m.group(1), m.group(2)
        if _host_allowed(url, allowed):
            links.append(m.group(0))
            return f"\x00{len(links) - 1}\x00"
        return f"{label} ({_defang(url)})" if label.strip() else _defang(url)

    text = _MD_LINK.sub(md_link, text)
    text = _BARE_URL.sub(lambda m: m.group(0) if _host_allowed(m.group(0), allowed) else _defang(m.group(0)), text)
    text = _WWW.sub(lambda m: m.group(0) if _host_allowed(m.group(0), allowed) else _defang(m.group(0)), text)
    text = _HTML_TAG.sub("&lt;", text)
    text = _MENTION.sub("@" + ZWSP, text)
    text = _SLASH_CMD.sub(lambda m: m.group(1) + ZWSP + "/", text)
    return re.sub("\x00(\\d+)\x00", lambda m: links[int(m.group(1))], text)


def sanitize_review(review: str, allowed_hosts=None) -> str:
    """Return a copy of the review that is safe to post publicly."""
    allowed = tuple(allowed_hosts) if allowed_hosts is not None else _allowed_hosts()
    text = redact_secrets(review or "").replace("\x00", "")

    out, prose, in_fence, fence_char = [], [], False, ""
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if not in_fence and m:
            out.append(_sanitize_prose("\n".join(prose), allowed))
            prose, in_fence, fence_char = [], True, m.group(1)
            out.append(line)
        elif in_fence:
            out.append(line)
            if m and m.group(1) == fence_char:
                in_fence = False
        else:
            prose.append(line)
    if prose:
        out.append(_sanitize_prose("\n".join(prose), allowed))
    if in_fence:   # an unclosed fence would swallow everything posted after it
        out.append(fence_char)
    result = "\n".join(s for s in out if s is not None)

    if len(result) > MAX_COMMENT_CHARS:
        result = result[:MAX_COMMENT_CHARS] + "\n\n_(review truncated)_"
    return result
