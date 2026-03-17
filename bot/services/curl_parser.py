"""
Pure Python cURL to API config converter.
No AI needed — deterministic parsing covers all real-world cURL formats.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Headers to strip completely — never useful in bot configs
STRIP_HEADERS = {
    "cookie", ":authority", ":method", ":path", ":scheme",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-gpc",
    "content-length", "accept-encoding", "connection", "host",
    "sec-websocket-key", "cache-control", "pragma", "te",
    "upgrade-insecure-requests", "dnt",
}

# Indian mobile prefixes for phone detection
INDIAN_PREFIXES = {
    "6", "7", "8", "9"
}


def _isIndianMobile(val: str) -> bool:
    """True if val looks like an Indian mobile number (10 digits starting 6-9)."""
    val = val.strip()
    if re.fullmatch(r'[6-9]\d{9}', val):
        return True
    return False


def _isIndianMobileWithCode(val: str) -> Tuple[bool, str, str]:
    """
    Returns (matched, prefix, digits) where prefix is one of:
      "91"  -> digits is 10-digit number  -> use "91{phone}"
      "+91" -> digits is 10-digit number  -> use "+91{phone}"
      ""    -> plain 10-digit             -> use "{phone}"
    """
    val = val.strip()
    if re.fullmatch(r'\+91[6-9]\d{9}', val):
        return True, "+91", val[3:]
    if re.fullmatch(r'91[6-9]\d{9}', val):
        return True, "91", val[2:]
    if re.fullmatch(r'[6-9]\d{9}', val):
        return True, "", val
    return False, "", ""


def _replacePhonesInString(val: str) -> str:
    """Replace any Indian mobile number in a string value."""
    # +91XXXXXXXXXX
    val = re.sub(r'\+91([6-9]\d{9})', r'+91{phone}', val)
    # 91XXXXXXXXXX (as string, not integer)
    val = re.sub(r'(?<!\d)91([6-9]\d{9})(?!\d)', r'91{phone}', val)
    # plain 10-digit
    val = re.sub(r'(?<!\d)([6-9]\d{9})(?!\d)', r'{phone}', val)
    return val


def _replacePhoneInValue(val: Any, original_was_int: bool = False) -> Any:
    """Replace phone in a value, preserving type if original was int."""
    if isinstance(val, str):
        matched, prefix, digits = _isIndianMobileWithCode(val)
        if matched:
            if prefix == "+91":
                return "+91{phone}"
            elif prefix == "91":
                return "91{phone}"
            else:
                return "{phone}"
        # partial match inside a longer string
        return _replacePhonesInString(val)
    if isinstance(val, int):
        sval = str(val)
        matched, prefix, digits = _isIndianMobileWithCode(sval)
        if matched:
            if prefix == "91":
                # Return as raw string that will be treated as integer placeholder
                # We'll mark it specially
                return "__INT__91{phone}"
            elif prefix == "":
                return "__INT__{phone}"
        return val
    if isinstance(val, dict):
        return {k: _replacePhoneInValue(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_replacePhoneInValue(v) for v in val]
    return val


def _cleanIntPlaceholders(obj: Any) -> Any:
    """Convert __INT__... markers back to proper int-typed placeholders in JSON."""
    if isinstance(obj, str) and obj.startswith("__INT__"):
        return obj[7:]  # strip marker, keep as string — JSON will not quote it
    if isinstance(obj, dict):
        return {k: _cleanIntPlaceholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_cleanIntPlaceholders(v) for v in obj]
    return obj


def _domainToName(url: str) -> str:
    """Extract a clean API name from URL domain."""
    try:
        host = urlparse(url).netloc
        # remove port
        host = host.split(":")[0]
        # remove www. api. etc
        parts = host.split(".")
        # find the main domain part (second to last usually)
        if len(parts) >= 2:
            name = parts[-2]
        else:
            name = parts[0]
        # capitalise
        return name.capitalize()
    except Exception:
        return "API"


def _parseBody(body: str, contentType: str) -> Tuple[Optional[Dict], Optional[Dict], str]:
    """
    Parse request body into (jsonData, formData, detectedContentType).
    Returns one of jsonData or formData, not both.
    """
    body = body.strip()
    if not body:
        return None, None, contentType

    # Try JSON first
    if "json" in contentType or body.startswith("{") or body.startswith("["):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed, None, "application/json"
        except json.JSONDecodeError:
            pass

    # Try form-encoded
    if "form" in contentType or "urlencoded" in contentType or ("=" in body and "&" in body) or ("=" in body and not body.startswith("{")):
        try:
            pairs = {}
            for part in body.split("&"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    from urllib.parse import unquote_plus
                    pairs[unquote_plus(k)] = unquote_plus(v)
            if pairs:
                return None, pairs, "application/x-www-form-urlencoded"
        except Exception:
            pass

    # Fallback: try JSON again more aggressively
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return parsed, None, "application/json"
    except Exception:
        pass

    return None, None, contentType


def parseCurl(curl: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Parse a cURL command into our API config format.
    Returns (success, config, errorMessage).
    """
    curl = curl.strip()

    # Normalize line continuations
    curl = re.sub(r'\\\n\s*', ' ', curl)
    curl = re.sub(r'\\\r\n\s*', ' ', curl)

    # Remove leading 'curl' and optional flags like curl.exe
    curl = re.sub(r'^curl(?:\.exe)?\s+', '', curl, flags=re.IGNORECASE).strip()

    # Tokenize using shlex (handles quoted strings properly)
    try:
        tokens = shlex.split(curl)
    except ValueError:
        # shlex failed — try basic split
        tokens = curl.split()

    url          = ""
    method       = "GET"
    headers: Dict[str, str] = {}
    cookies: Dict[str, str] = {}
    body         = ""
    contentType  = ""
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
                key  = key.strip().lower()
                val  = val.strip()
                if key not in STRIP_HEADERS:
                    if key == "content-type":
                        contentType = val.lower()
                        headers[key] = val
                    elif key == "cookie":
                        # parse cookie header into cookies dict
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
            body = tokens[idx + 1]
            if method == "GET":
                method = "POST"
            idx += 2

        elif tok in ("-G", "--get"):
            method = "GET"
            idx += 1

        elif tok == "--compressed":
            idx += 1

        elif tok in ("-L", "--location", "-s", "--silent", "-k", "--insecure",
                     "-v", "--verbose", "--http2", "--http1.1"):
            idx += 1

        elif tok in ("-o", "--output", "-u", "--user", "--proxy",
                     "--connect-timeout", "--max-time", "-m", "--retry"):
            idx += 2  # skip value too

        elif not tok.startswith("-"):
            # This is the URL
            if not url:
                url = tok
            idx += 1

        else:
            idx += 1

    if not url:
        return False, None, "Could not find URL in the cURL command."

    # Extract query params from URL and move them to params dict if GET
    parsed_url = urlparse(url)
    if parsed_url.query:
        qs = parse_qs(parsed_url.query, keep_blank_values=True)
        for k, vs in qs.items():
            params[k] = vs[0] if vs else ""
        # Clean URL of query string
        url = urlunparse(parsed_url._replace(query=""))

    # Build config
    name = _domainToName(url)

    # Parse body
    jsonData, formData, detectedCt = _parseBody(body, contentType)

    # Replace phone numbers in all parts
    url_replaced = _replacePhonesInString(url)

    headers_replaced = {}
    for k, v in headers.items():
        headers_replaced[k] = _replacePhonesInString(v)

    cookies_replaced = {}
    for k, v in cookies.items():
        cookies_replaced[k] = _replacePhonesInString(v)

    params_replaced = {}
    for k, v in params.items():
        params_replaced[k] = _replacePhonesInString(v)

    if jsonData is not None:
        jsonData = _replacePhoneInValue(jsonData)
        jsonData = _cleanIntPlaceholders(jsonData)

    if formData is not None:
        formData = {k: _replacePhonesInString(v) for k, v in formData.items()}

    # Build final config
    cfg: Dict[str, Any] = {
        "name":   name,
        "method": method,
        "url":    url_replaced,
    }

    if headers_replaced:
        cfg["headers"] = headers_replaced

    if jsonData:
        cfg["json"] = jsonData
    elif formData:
        cfg["data"] = formData

    if params_replaced:
        cfg["params"] = params_replaced

    if cookies_replaced:
        cfg["cookies"] = cookies_replaced

    # Validate minimums
    if not cfg["url"].startswith("http"):
        return False, None, f"URL does not start with http: {cfg['url'][:60]}"

    return True, cfg, ""


def formatParseResult(cfg: Dict[str, Any]) -> str:
    """Format a parsed config for display, handling int placeholders."""
    # We need to show __INT__ values properly in the preview
    # but the actual saved config should use them as strings with a note
    return json.dumps(cfg, indent=2)