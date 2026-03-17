"""
Pure Python cURL to API config converter.
Handles both bash-style and Windows CMD-style cURL (from Chrome DevTools on Windows).
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlunparse, unquote_plus

STRIP_HEADERS = {
    "cookie", ":authority", ":method", ":path", ":scheme",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-storage-access",
    "sec-gpc", "content-length", "accept-encoding", "connection", "host",
    "sec-websocket-key", "cache-control", "pragma", "te",
    "upgrade-insecure-requests", "dnt", "priority", "traceparent",
}


def _isIndianMobileWithCode(val: str) -> Tuple[bool, str, str]:
    val = val.strip()
    if re.fullmatch(r'\+91[6-9]\d{9}', val):
        return True, "+91", val[3:]
    if re.fullmatch(r'91[6-9]\d{9}', val):
        return True, "91", val[2:]
    if re.fullmatch(r'[6-9]\d{9}', val):
        return True, "", val
    return False, "", ""


def _replacePhonesInString(val: str) -> str:
    val = re.sub(r'\+91([6-9]\d{9})', r'+91{phone}', val)
    val = re.sub(r'(?<!\d)91([6-9]\d{9})(?!\d)', r'91{phone}', val)
    val = re.sub(r'(?<!\d)([6-9]\d{9})(?!\d)', r'{phone}', val)
    return val


def _replacePhoneInValue(val: Any) -> Any:
    if isinstance(val, str):
        matched, prefix, _ = _isIndianMobileWithCode(val)
        if matched:
            return f"{prefix}{{phone}}" if prefix else "{phone}"
        return _replacePhonesInString(val)
    if isinstance(val, int):
        sval = str(val)
        matched, prefix, _ = _isIndianMobileWithCode(sval)
        if matched:
            # Return as string placeholder — will be noted to user
            return f"__INT__{prefix}{{phone}}" if prefix else "__INT__{phone}"
        return val
    if isinstance(val, dict):
        return {k: _replacePhoneInValue(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_replacePhoneInValue(v) for v in val]
    return val


def _cleanIntPlaceholders(obj: Any) -> Any:
    if isinstance(obj, str) and obj.startswith("__INT__"):
        return obj[7:]
    if isinstance(obj, dict):
        return {k: _cleanIntPlaceholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_cleanIntPlaceholders(v) for v in obj]
    return obj


def _domainToName(url: str) -> str:
    try:
        host  = urlparse(url).netloc.split(":")[0]
        parts = host.split(".")
        name  = parts[-2] if len(parts) >= 2 else parts[0]
        return name.capitalize()
    except Exception:
        return "API"


def _parseBody(body: str, contentType: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    body = body.strip()
    if not body:
        return None, None
    if "json" in contentType or body.startswith("{") or body.startswith("["):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed, None
        except json.JSONDecodeError:
            pass
    if "form" in contentType or "urlencoded" in contentType or (
        "=" in body and not body.startswith("{")
    ):
        try:
            pairs = {}
            # CMD escapes & as ^& — clean it
            body_clean = body.replace('^&', '&').replace('^=', '=')
            for part in body_clean.split("&"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    pairs[unquote_plus(k)] = unquote_plus(v)
            if pairs:
                return None, pairs
        except Exception:
            pass
    # last attempt JSON
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return parsed, None
    except Exception:
        pass
    return None, None


def _extractCmdBody(curl: str) -> Tuple[Optional[str], str]:
    """
    Extract --data-raw value from Windows CMD cURL BEFORE normalization.
    CMD format: --data-raw ^"^{^\\^"key^\\^":^\\^"val^\\^"^}^"
    Returns (body_string, curl_with_body_token_replaced).
    """
    pattern = r'((?:--data-raw|--data-binary|--data-urlencode|-d)\s+)\^"(.*?)\^"(?=\s|$)'
    m = re.search(pattern, curl, re.DOTALL)
    if not m:
        return None, curl
    raw = m.group(2)
    # Unescape CMD inner escaping
    raw = raw.replace(r'^\^"', '"')   # ^\^" -> "
    raw = raw.replace('^{', '{').replace('^}', '}')
    raw = raw.replace('^[', '[').replace('^]', ']')
    raw = raw.replace('^^', '^')
    # Replace the matched section in the original curl with a safe placeholder
    placeholder = m.group(1) + "'__BODY_PLACEHOLDER__'"
    curl_clean  = curl[:m.start()] + placeholder + curl[m.end():]
    return raw, curl_clean


def _normalizeCmdCurl(curl: str) -> str:
    """Convert Windows CMD cURL escape syntax to bash-style."""
    # Join CMD line continuations: ^ at end of line
    curl = re.sub(r'\^ *\r?\n\s*', ' ', curl)
    # Replace ^" (CMD outer quoting) with regular "
    curl = curl.replace('^"', '"')
    # Remove stray ^ that CMD uses as line escape (not inside strings)
    curl = re.sub(r'\^(?!")', '', curl)
    return curl


def parseCurl(curl: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Parse a cURL command into API config format.
    Returns (success, config, errorMessage).
    """
    curl = curl.strip()

    # Detect Windows CMD format
    isCmdFormat = '^"' in curl or re.search(r'\^\s*\r?\n', curl) is not None

    body = ""
    if isCmdFormat:
        # Extract body FIRST before normalization corrupts inner quotes
        extracted, curl = _extractCmdBody(curl)
        if extracted is not None:
            body = extracted
        curl = _normalizeCmdCurl(curl)
    else:
        # Bash: normalize backslash line continuations
        curl = re.sub(r'\\\n\s*', ' ', curl)
        curl = re.sub(r'\\\r\n\s*', ' ', curl)

    # Strip leading 'curl' / 'curl.exe'
    curl = re.sub(r'^curl(?:\.exe)?\s+', '', curl, flags=re.IGNORECASE).strip()

    # Tokenize
    try:
        tokens = shlex.split(curl)
    except ValueError:
        tokens = curl.split()

    url         = ""
    method      = "GET"
    headers: Dict[str, str] = {}
    cookies: Dict[str, str] = {}
    contentType = ""
    params: Dict[str, str] = {}

    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]

        if tok in ("-X", "--request") and idx + 1 < len(tokens):
            method = tokens[idx + 1].upper()
            idx += 2

        elif tok in ("-H", "--header") and idx + 1 < len(tokens):
            raw = tokens[idx + 1]
            if ":" in raw:
                key, _, val = raw.partition(":")
                key = key.strip().lower()
                val = val.strip()
                if key not in STRIP_HEADERS:
                    if key == "content-type":
                        contentType = val.lower()
                        headers[key] = val
                    elif key == "cookie":
                        for pair in val.split(";"):
                            pair = pair.strip()
                            if "=" in pair:
                                ck, _, cv = pair.partition("=")
                                cookies[ck.strip()] = cv.strip()
                    else:
                        headers[key] = val
            idx += 2

        elif tok in ("-b", "--cookie") and idx + 1 < len(tokens):
            raw = tokens[idx + 1]
            for pair in raw.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    ck, _, cv = pair.partition("=")
                    cookies[ck.strip()] = cv.strip()
            idx += 2

        elif tok in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode") and idx + 1 < len(tokens):
            val = tokens[idx + 1]
            if val != "__BODY_PLACEHOLDER__" and not body:
                body = val
            elif val == "__BODY_PLACEHOLDER__":
                pass  # already extracted above
            if method == "GET":
                method = "POST"
            idx += 2

        elif tok in ("-G", "--get"):
            method = "GET"
            idx += 1

        elif tok in ("--compressed", "-L", "--location", "-s", "--silent",
                     "-k", "--insecure", "-v", "--verbose",
                     "--http2", "--http1.1"):
            idx += 1

        elif tok in ("-o", "--output", "-u", "--user", "--proxy",
                     "--connect-timeout", "--max-time", "-m", "--retry"):
            idx += 2

        elif not tok.startswith("-"):
            if not url:
                url = tok.lstrip("^")
            idx += 1

        else:
            idx += 1

    if not url:
        return False, None, "Could not find URL in the cURL command."

    # Strip any stray ^ that Telegram may preserve from CMD format
    url = url.lstrip("^")
    if not url.startswith("http"):
        return False, None, f"URL does not start with http — got: {url[:80]}"

    # Extract query params from URL into params dict
    parsed_url = urlparse(url)
    if parsed_url.query:
        qs = parse_qs(parsed_url.query, keep_blank_values=True)
        for k, vs in qs.items():
            params[k] = vs[0] if vs else ""
        url = urlunparse(parsed_url._replace(query=""))

    if body and method == "GET":
        method = "POST"

    name = _domainToName(url)

    # Parse body
    jsonData, formData = _parseBody(body, contentType)

    # Replace phones everywhere
    url_final     = _replacePhonesInString(url)
    headers_final = {k: _replacePhonesInString(v) for k, v in headers.items()}
    cookies_final = {k: _replacePhonesInString(v) for k, v in cookies.items()}
    params_final  = {k: _replacePhonesInString(v) for k, v in params.items()}

    if jsonData is not None:
        jsonData = _replacePhoneInValue(jsonData)
        jsonData = _cleanIntPlaceholders(jsonData)
    if formData is not None:
        formData = {k: _replacePhonesInString(v) for k, v in formData.items()}

    cfg: Dict[str, Any] = {
        "name":   name,
        "method": method,
        "url":    url_final,
    }
    if headers_final:
        cfg["headers"] = headers_final
    if jsonData:
        cfg["json"] = jsonData
    elif formData:
        cfg["data"] = formData
    if params_final:
        cfg["params"] = params_final
    if cookies_final:
        cfg["cookies"] = cookies_final

    return True, cfg, ""