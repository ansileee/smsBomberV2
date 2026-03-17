from __future__ import annotations

import json
import time
from typing import List, Dict, Any, Tuple, Optional

from apis import API_CONFIGS as _BASE_CONFIGS  # type: ignore
from bot.services.database import db

# Cache merged configs for 30 seconds — avoids rebuilding on every button press
_cache: List[Dict[str, Any]] = []
_cacheTime: float = 0.0
_CACHE_TTL: float = 30.0


class ApiManager:
    def getMergedConfigs(self) -> List[Dict[str, Any]]:
        global _cache, _cacheTime
        now = time.time()
        if _cache and (now - _cacheTime) < _CACHE_TTL:
            return _cache

        customApis = db.getAllCustomApis()
        customByName: Dict[str, Dict] = {}
        customByUrl:  Dict[str, Dict] = {}
        for row in customApis:
            try:
                cfg = json.loads(row["configJson"])
                customByName[cfg.get("name", "").lower()] = cfg
                customByUrl[cfg.get("url", "")]           = cfg
            except Exception:
                pass

        result    = []
        seenNames = set()

        for base in _BASE_CONFIGS:
            key      = base["name"].lower()
            override = customByName.get(key) or customByUrl.get(base["url"])
            if override:
                result.append(override)
                seenNames.add(override.get("name", "").lower())
            else:
                result.append(base)
                seenNames.add(key)

        for row in customApis:
            try:
                cfg = json.loads(row["configJson"])
                if cfg.get("name", "").lower() not in seenNames:
                    result.append(cfg)
            except Exception:
                pass

        skipped = db.getSkippedApiNames()
        result  = [cfg for cfg in result if cfg.get("name", "") not in skipped]

        _cache     = result
        _cacheTime = now
        return result

    def invalidateCache(self) -> None:
        """Call after adding/editing/deleting APIs so next call rebuilds."""
        global _cache, _cacheTime
        _cache     = []
        _cacheTime = 0.0

    def validateApiJson(self, raw: str) -> Tuple[bool, Optional[Dict], str]:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = [l for l in raw.splitlines() if not l.startswith("```")]
            raw   = "\n".join(lines).strip()
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError as ex:
            return False, None, f"Invalid JSON: {ex}"
        if not isinstance(cfg, dict):
            return False, None, "JSON must be an object, not a list or value."
        for field in ["name", "method", "url"]:
            if field not in cfg:
                return False, None, f'Missing required field: "{field}"'
        if cfg["method"].upper() not in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
            return False, None, f"Invalid method: {cfg['method']}"
        if not cfg["url"].startswith("http"):
            return False, None, "URL must start with http:// or https://"
        cfg["method"] = cfg["method"].upper()
        return True, cfg, ""

    def formatApiPreview(self, cfg: dict) -> str:
        lines = [
            "API Preview\n",
            f"Name   : {cfg['name']}",
            f"Method : {cfg['method']}",
            f"URL    : {cfg['url']}",
        ]
        if cfg.get("headers"):
            lines.append(f"Headers: {len(cfg['headers'])} defined")
        if cfg.get("json"):
            lines.append(f"Body   : JSON ({len(cfg['json'])} fields)")
        elif cfg.get("data"):
            lines.append(f"Body   : Form data ({len(cfg['data'])} fields)")
        if cfg.get("params"):
            lines.append(f"Params : {len(cfg['params'])} defined")
        return "\n".join(lines)


apiManager = ApiManager()