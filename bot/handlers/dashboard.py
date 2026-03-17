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


def _getConn():
    """Return a connection — Turso if configured, else local SQLite."""
    if TURSO_URL and TURSO_TOKEN:
        import libsql_experimental as libsql  # type: ignore
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        import sqlite3
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn


class Database:
    def __init__(self) -> None:
        self._conn = _getConn()
        # libsql uses same API as sqlite3 but row_factory must be set differently
        if TURSO_URL and TURSO_TOKEN:
            import sqlite3 as _sq
            self._conn.row_factory = _sq.Row
        self._createTables()

    def _ex(self, sql: str, params: tuple = ()):
        """Execute and return cursor."""
        return self._conn.execute(sql, params)

    def _exs(self, script: str):
        try:
            self._conn.executescript(script)
        except AttributeError:
            # libsql may not have executescript — run statements one by one
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass
            self._conn.commit()

    def _createTables(self) -> None:
        self._exs("""
            CREATE TABLE IF NOT EXISTS users (
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
            );
            CREATE TABLE IF NOT EXISTS testHistory (
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
                finishedAt  REAL,
                FOREIGN KEY (userId) REFERENCES users(userId)
            );
            CREATE TABLE IF NOT EXISTS customApis (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                method      TEXT NOT NULL,
                url         TEXT NOT NULL,
                configJson  TEXT NOT NULL,
                addedAt     REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proxyFiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                content     TEXT NOT NULL,
                proxyCount  INTEGER NOT NULL DEFAULT 0,
                uploadedAt  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS blacklistedPhones (
                phone       TEXT PRIMARY KEY,
                reason      TEXT NOT NULL DEFAULT '',
                addedAt     REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skippedApis (
                name        TEXT PRIMARY KEY,
                addedAt     REAL NOT NULL
            )
        """)
        # safe migrations for existing DBs
        for col, definition in [
            ("testsTotal", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                self._conn.commit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def registerUser(self, userId: int, username: Optional[str], firstName: str, lastName: Optional[str]) -> bool:
        existing = self._ex("SELECT userId FROM users WHERE userId=?", (userId,)).fetchone()
        if existing:
            self._ex(
                "UPDATE users SET username=?, firstName=?, lastName=? WHERE userId=?",
                (username, firstName, lastName or "", userId)
            )
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
        row = self._ex("SELECT * FROM users WHERE userId=?", (userId,)).fetchone()
        return dict(row) if row else None

    def getAllUsers(self, offset: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._ex("SELECT * FROM users ORDER BY joinedAt DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(r) for r in rows]

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

    # ------------------------------------------------------------------
    # Daily limit
    # ------------------------------------------------------------------

    def _ensureResetForUser(self, userId: int) -> None:
        today = getIstToday()
        row   = self._ex("SELECT lastResetDate FROM users WHERE userId=?", (userId,)).fetchone()
        if row and row["lastResetDate"] != today:
            self._ex("UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?", (today, userId))
            self._conn.commit()

    def canRunTest(self, userId: int) -> tuple:
        self._ensureResetForUser(userId)
        row = self._ex(
            "SELECT testsToday, dailyLimit, isBanned FROM users WHERE userId=?", (userId,)
        ).fetchone()
        if not row:
            return False, 0, 0
        if row["isBanned"]:
            return False, row["testsToday"], row["dailyLimit"]
        return row["testsToday"] < row["dailyLimit"], row["testsToday"], row["dailyLimit"]

    def incrementTestCount(self, userId: int) -> None:
        self._ensureResetForUser(userId)
        self._ex(
            "UPDATE users SET testsToday=testsToday+1, testsTotal=testsTotal+1 WHERE userId=?", (userId,)
        )
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
        rows = self._ex(
            "SELECT * FROM testHistory WHERE userId=? ORDER BY startedAt DESC LIMIT ?", (userId, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    # last test config for repeat — stored in DB so it survives restarts
    def saveLastConfig(self, userId: int, phone: str, duration: int, workers: int) -> None:
        # reuse skippedApis-style pattern: store as a blob in a dedicated table
        # We just upsert into a simple key-value via customApis trick:
        # Actually use a dedicated table created below
        self._ex(
            "INSERT OR REPLACE INTO lastConfigs (userId, phone, duration, workers) VALUES (?,?,?,?)",
            (userId, phone, duration, workers)
        )
        self._conn.commit()

    def getLastConfig(self, userId: int) -> Optional[Dict[str, Any]]:
        try:
            row = self._ex("SELECT * FROM lastConfigs WHERE userId=?", (userId,)).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def _ensureLastConfigsTable(self) -> None:
        try:
            self._ex("""
                CREATE TABLE IF NOT EXISTS lastConfigs (
                    userId   INTEGER PRIMARY KEY,
                    phone    TEXT NOT NULL,
                    duration INTEGER NOT NULL,
                    workers  INTEGER NOT NULL
                )
            """)
            self._conn.commit()
        except Exception:
            pass

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
        rows = self._ex("SELECT * FROM customApis ORDER BY addedAt DESC").fetchall()
        return [dict(r) for r in rows]

    def getCustomApi(self, apiId: int) -> Optional[Dict[str, Any]]:
        row = self._ex("SELECT * FROM customApis WHERE id=?", (apiId,)).fetchone()
        return dict(row) if row else None

    def updateCustomApi(self, apiId: int, name: str, method: str, url: str, configJson: str) -> None:
        self._ex(
            "UPDATE customApis SET name=?,method=?,url=?,configJson=? WHERE id=?",
            (name, method, url, configJson, apiId)
        )
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
        rows = self._ex("SELECT * FROM proxyFiles ORDER BY uploadedAt DESC").fetchall()
        return [dict(r) for r in rows]

    def getProxyFile(self, fileId: int) -> Optional[Dict[str, Any]]:
        row = self._ex("SELECT * FROM proxyFiles WHERE id=?", (fileId,)).fetchone()
        return dict(row) if row else None

    def deleteProxyFile(self, fileId: int) -> None:
        self._ex("DELETE FROM proxyFiles WHERE id=?", (fileId,))
        self._conn.commit()

    def getAllProxies(self) -> List[str]:
        rows = self._ex("SELECT content FROM proxyFiles").fetchall()
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
        self._ex(
            "INSERT OR REPLACE INTO blacklistedPhones (phone,reason,addedAt) VALUES (?,?,?)",
            (phone, reason, time.time())
        )
        self._conn.commit()

    def unblacklistPhone(self, phone: str) -> None:
        self._ex("DELETE FROM blacklistedPhones WHERE phone=?", (phone,))
        self._conn.commit()

    def isPhoneBlacklisted(self, phone: str) -> bool:
        return self._ex("SELECT phone FROM blacklistedPhones WHERE phone=?", (phone,)).fetchone() is not None

    def getAllBlacklisted(self) -> List[Dict[str, Any]]:
        rows = self._ex("SELECT * FROM blacklistedPhones ORDER BY addedAt DESC").fetchall()
        return [dict(r) for r in rows]

    def bulkBlacklist(self, phones: List[str], reason: str = "") -> int:
        now   = time.time()
        added = 0
        for phone in phones:
            phone = phone.strip()
            if phone.isdigit() and len(phone) == 10:
                self._ex(
                    "INSERT OR REPLACE INTO blacklistedPhones (phone,reason,addedAt) VALUES (?,?,?)",
                    (phone, reason, now)
                )
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
        rows = self._ex("SELECT name FROM skippedApis").fetchall()
        return {r["name"] for r in rows}

    def isApiSkipped(self, name: str) -> bool:
        return self._ex("SELECT name FROM skippedApis WHERE name=?", (name,)).fetchone() is not None

    def close(self) -> None:
        self._conn.close()


db = Database()
db._ensureLastConfigsTable()