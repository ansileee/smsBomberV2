from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from bot.config import DB_FILE, DEFAULT_DAILY_LIMIT, IST_OFFSET_HOURS, TURSO_URL, TURSO_TOKEN

IST = timezone(timedelta(hours=IST_OFFSET_HOURS))


def getIstToday() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def getSecondsUntilMidnightIst() -> float:
    now      = datetime.now(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - now).total_seconds()


class Database:
    def __init__(self) -> None:
        if TURSO_URL and TURSO_TOKEN:
            import libsql_experimental as libsql  # type: ignore
            self._conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            self._turso = True
        else:
            import sqlite3
            self._conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._turso = False
        self._createTables()

    def _row(self, cursor) -> Optional[Dict[str, Any]]:
        """Fetch one row as dict regardless of backend."""
        row = cursor.fetchone()
        if row is None:
            return None
        if hasattr(row, 'keys'):
            return dict(row)
        # libsql returns tuples — get column names from cursor description
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def _rows(self, cursor) -> List[Dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        rows = cursor.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], 'keys'):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in rows]

    def _ex(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql, params)

    def _createTables(self) -> None:
        stmts = [
            """CREATE TABLE IF NOT EXISTS users (
                userId        INTEGER PRIMARY KEY,
                username      TEXT,
                firstName     TEXT,
                lastName      TEXT,
                joinedAt      REAL NOT NULL,
                isBanned      INTEGER NOT NULL DEFAULT 0,
                dailyLimit    INTEGER NOT NULL DEFAULT 10,
                testsToday    INTEGER NOT NULL DEFAULT 0,
                testsTotal    INTEGER NOT NULL DEFAULT 0,
                lastResetDate TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS testHistory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                userId      INTEGER NOT NULL,
                phone       TEXT NOT NULL,
                duration    INTEGER NOT NULL,
                workers     INTEGER NOT NULL,
                totalReqs   INTEGER NOT NULL DEFAULT 0,
                otpHits     INTEGER NOT NULL DEFAULT 0,
                errors      INTEGER NOT NULL DEFAULT 0,
                rps         REAL NOT NULL DEFAULT 0,
                startedAt   REAL NOT NULL,
                finishedAt  REAL
            )""",
            """CREATE TABLE IF NOT EXISTS customApis (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                method      TEXT NOT NULL,
                url         TEXT NOT NULL,
                configJson  TEXT NOT NULL,
                addedAt     REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS proxyFiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                content     TEXT NOT NULL,
                proxyCount  INTEGER NOT NULL DEFAULT 0,
                uploadedAt  REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS blacklistedPhones (
                phone       TEXT PRIMARY KEY,
                reason      TEXT NOT NULL DEFAULT '',
                addedAt     REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS skippedApis (
                name        TEXT PRIMARY KEY,
                addedAt     REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS lastConfigs (
                userId   INTEGER PRIMARY KEY,
                phone    TEXT NOT NULL,
                duration INTEGER NOT NULL,
                workers  INTEGER NOT NULL
            )""",
        ]
        for stmt in stmts:
            try:
                self._conn.execute(stmt)
            except Exception:
                pass
        self._conn.commit()
        # safe migrations
        for col, definition in [("testsTotal", "INTEGER NOT NULL DEFAULT 0")]:
            try:
                self._conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                self._conn.commit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def registerUser(self, userId: int, username: Optional[str], firstName: str, lastName: Optional[str]) -> bool:
        cur = self._ex("SELECT userId FROM users WHERE userId=?", (userId,))
        if self._row(cur):
            self._ex("UPDATE users SET username=?, firstName=?, lastName=? WHERE userId=?",
                     (username, firstName, lastName or "", userId))
            self._conn.commit()
            return False
        self._ex(
            "INSERT INTO users (userId,username,firstName,lastName,joinedAt,dailyLimit,lastResetDate,testsToday,testsTotal) "
            "VALUES (?,?,?,?,?,?,?,0,0)",
            (userId, username, firstName, lastName or "", time.time(), DEFAULT_DAILY_LIMIT, getIstToday())
        )
        self._conn.commit()
        return True

    def getUser(self, userId: int) -> Optional[Dict[str, Any]]:
        return self._row(self._ex("SELECT * FROM users WHERE userId=?", (userId,)))

    def getAllUsers(self, offset: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
        return self._rows(self._ex(
            "SELECT * FROM users ORDER BY joinedAt DESC LIMIT ? OFFSET ?", (limit, offset)
        ))

    def getUserCount(self) -> int:
        return self._ex("SELECT COUNT(*) FROM users").fetchone()[0]

    def banUser(self, userId: int) -> None:
        self._ex("UPDATE users SET isBanned=1 WHERE userId=?", (userId,))
        self._conn.commit()

    def unbanUser(self, userId: int) -> None:
        self._ex("UPDATE users SET isBanned=0 WHERE userId=?", (userId,))
        self._conn.commit()

    def setDailyLimit(self, userId: int, limit: int) -> None:
        self._ex("UPDATE users SET dailyLimit=? WHERE userId=?", (limit, userId))
        self._conn.commit()

    def setGlobalDailyLimit(self, limit: int) -> None:
        self._ex("UPDATE users SET dailyLimit=?", (limit,))
        self._conn.commit()

    def _ensureResetForUser(self, userId: int) -> None:
        today = getIstToday()
        cur   = self._ex("SELECT lastResetDate FROM users WHERE userId=?", (userId,))
        row   = self._row(cur)
        if row and row["lastResetDate"] != today:
            self._ex("UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?", (today, userId))
            self._conn.commit()

    def canRunTest(self, userId: int) -> tuple:
        self._ensureResetForUser(userId)
        row = self._row(self._ex(
            "SELECT testsToday, dailyLimit, isBanned FROM users WHERE userId=?", (userId,)
        ))
        if not row:
            return False, 0, 0
        if row["isBanned"]:
            return False, row["testsToday"], row["dailyLimit"]
        return row["testsToday"] < row["dailyLimit"], row["testsToday"], row["dailyLimit"]

    def incrementTestCount(self, userId: int) -> None:
        self._ensureResetForUser(userId)
        self._ex("UPDATE users SET testsToday=testsToday+1, testsTotal=testsTotal+1 WHERE userId=?", (userId,))
        self._conn.commit()

    def resetUserTests(self, userId: int) -> None:
        self._ex("UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?", (getIstToday(), userId))
        self._conn.commit()

    def resetAllTests(self) -> None:
        self._ex("UPDATE users SET testsToday=0, lastResetDate=?", (getIstToday(),))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Test history
    # ------------------------------------------------------------------

    def startTestRecord(self, userId: int, phone: str, duration: int, workers: int) -> int:
        cur = self._ex(
            "INSERT INTO testHistory (userId,phone,duration,workers,startedAt) VALUES (?,?,?,?,?)",
            (userId, phone, duration, workers, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def finishTestRecord(self, recordId: int, totalReqs: int, otpHits: int, errors: int, rps: float) -> None:
        self._ex(
            "UPDATE testHistory SET totalReqs=?,otpHits=?,errors=?,rps=?,finishedAt=? WHERE id=?",
            (totalReqs, otpHits, errors, rps, time.time(), recordId)
        )
        self._conn.commit()

    def getUserHistory(self, userId: int, limit: int = 10) -> List[Dict[str, Any]]:
        return self._rows(self._ex(
            "SELECT * FROM testHistory WHERE userId=? ORDER BY startedAt DESC LIMIT ?", (userId, limit)
        ))

    def saveLastConfig(self, userId: int, phone: str, duration: int, workers: int) -> None:
        self._ex(
            "INSERT OR REPLACE INTO lastConfigs (userId,phone,duration,workers) VALUES (?,?,?,?)",
            (userId, phone, duration, workers)
        )
        self._conn.commit()

    def getLastConfig(self, userId: int) -> Optional[Dict[str, Any]]:
        try:
            return self._row(self._ex("SELECT * FROM lastConfigs WHERE userId=?", (userId,)))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Custom APIs
    # ------------------------------------------------------------------

    def addCustomApi(self, name: str, method: str, url: str, configJson: str) -> int:
        cur = self._ex(
            "INSERT INTO customApis (name,method,url,configJson,addedAt) VALUES (?,?,?,?,?)",
            (name, method, url, configJson, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def getAllCustomApis(self) -> List[Dict[str, Any]]:
        return self._rows(self._ex("SELECT * FROM customApis ORDER BY addedAt DESC"))

    def getCustomApi(self, apiId: int) -> Optional[Dict[str, Any]]:
        return self._row(self._ex("SELECT * FROM customApis WHERE id=?", (apiId,)))

    def updateCustomApi(self, apiId: int, name: str, method: str, url: str, configJson: str) -> None:
        self._ex("UPDATE customApis SET name=?,method=?,url=?,configJson=? WHERE id=?",
                 (name, method, url, configJson, apiId))
        self._conn.commit()

    def deleteCustomApi(self, apiId: int) -> None:
        self._ex("DELETE FROM customApis WHERE id=?", (apiId,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Proxy files
    # ------------------------------------------------------------------

    def addProxyFile(self, label: str, content: str, proxyCount: int) -> int:
        cur = self._ex(
            "INSERT INTO proxyFiles (label,content,proxyCount,uploadedAt) VALUES (?,?,?,?)",
            (label, content, proxyCount, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def getAllProxyFiles(self) -> List[Dict[str, Any]]:
        return self._rows(self._ex("SELECT * FROM proxyFiles ORDER BY uploadedAt DESC"))

    def getProxyFile(self, fileId: int) -> Optional[Dict[str, Any]]:
        return self._row(self._ex("SELECT * FROM proxyFiles WHERE id=?", (fileId,)))

    def deleteProxyFile(self, fileId: int) -> None:
        self._ex("DELETE FROM proxyFiles WHERE id=?", (fileId,))
        self._conn.commit()

    def getAllProxies(self) -> List[str]:
        rows    = self._rows(self._ex("SELECT content FROM proxyFiles"))
        proxies = []
        for r in rows:
            for line in r["content"].splitlines():
                line = line.strip()
                if line:
                    proxies.append(line)
        return proxies

    # ------------------------------------------------------------------
    # Phone blacklist
    # ------------------------------------------------------------------

    def blacklistPhone(self, phone: str, reason: str = "") -> None:
        self._ex("INSERT OR REPLACE INTO blacklistedPhones (phone,reason,addedAt) VALUES (?,?,?)",
                 (phone, reason, time.time()))
        self._conn.commit()

    def unblacklistPhone(self, phone: str) -> None:
        self._ex("DELETE FROM blacklistedPhones WHERE phone=?", (phone,))
        self._conn.commit()

    def isPhoneBlacklisted(self, phone: str) -> bool:
        return self._row(self._ex(
            "SELECT phone FROM blacklistedPhones WHERE phone=?", (phone,)
        )) is not None

    def getAllBlacklisted(self) -> List[Dict[str, Any]]:
        return self._rows(self._ex("SELECT * FROM blacklistedPhones ORDER BY addedAt DESC"))

    def bulkBlacklist(self, phones: List[str], reason: str = "") -> int:
        now   = time.time()
        added = 0
        for phone in phones:
            phone = phone.strip()
            if phone.isdigit() and len(phone) == 10:
                self._ex("INSERT OR REPLACE INTO blacklistedPhones (phone,reason,addedAt) VALUES (?,?,?)",
                         (phone, reason, now))
                added += 1
        self._conn.commit()
        return added

    # ------------------------------------------------------------------
    # Skipped APIs
    # ------------------------------------------------------------------

    def skipApi(self, name: str) -> None:
        self._ex("INSERT OR REPLACE INTO skippedApis (name,addedAt) VALUES (?,?)", (name, time.time()))
        self._conn.commit()

    def unskipApi(self, name: str) -> None:
        self._ex("DELETE FROM skippedApis WHERE name=?", (name,))
        self._conn.commit()

    def getSkippedApiNames(self) -> set:
        rows = self._rows(self._ex("SELECT name FROM skippedApis"))
        return {r["name"] for r in rows}

    def isApiSkipped(self, name: str) -> bool:
        return self._row(self._ex("SELECT name FROM skippedApis WHERE name=?", (name,))) is not None

    def close(self) -> None:
        self._conn.close()


db = Database()