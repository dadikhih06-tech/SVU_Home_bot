"""
قاعدة البيانات SQLite - مخطط التخصصات والكتل
الهيكل: تخصص ← خدمة (سعر) ← كتلة ← مادة
مع سلة مشتريات عبر الكتل ضمن نفس (التخصص + الخدمة)
"""
import aiosqlite
from typing import Optional
import asyncio

from config import logger

DB_PATH = "bot_database.db"

SCHEMA_SQL = """
-- التخصصات
CREATE TABLE IF NOT EXISTS specializations (
    name TEXT PRIMARY KEY
);

-- الخدمات والأسعار (لكل مادة)
CREATE TABLE IF NOT EXISTS services (
    name TEXT PRIMARY KEY,
    price INTEGER NOT NULL
);

-- الخدمات المتاحة لكل تخصص
CREATE TABLE IF NOT EXISTS spec_services (
    spec_name TEXT NOT NULL,
    service_name TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    PRIMARY KEY (spec_name, service_name),
    FOREIGN KEY (spec_name) REFERENCES specializations(name) ON DELETE CASCADE,
    FOREIGN KEY (service_name) REFERENCES services(name) ON DELETE CASCADE
);

-- كتل المقررات لكل تخصص
CREATE TABLE IF NOT EXISTS spec_blocks (
    spec_name TEXT NOT NULL,
    block_name TEXT NOT NULL,
    PRIMARY KEY (spec_name, block_name),
    FOREIGN KEY (spec_name) REFERENCES specializations(name) ON DELETE CASCADE
);

-- المواد (القائمة الرئيسية)
CREATE TABLE IF NOT EXISTS courses (
    name TEXT PRIMARY KEY
);

-- ربط المواد بالكتل (تفعيل/تعطيل)
CREATE TABLE IF NOT EXISTS block_courses (
    spec_name TEXT NOT NULL,
    block_name TEXT NOT NULL,
    course_name TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    PRIMARY KEY (spec_name, block_name, course_name),
    FOREIGN KEY (spec_name) REFERENCES specializations(name) ON DELETE CASCADE,
    FOREIGN KEY (course_name) REFERENCES courses(name) ON DELETE CASCADE
);

-- المستخدمون
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- الطلبات (specialization = التخصص، courses = JSON بالمواد من السلة)
CREATE TABLE IF NOT EXISTS orders (
    order_key TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    full_name TEXT,
    username TEXT,
    specialization TEXT,
    service TEXT NOT NULL,
    courses TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    total_price INTEGER NOT NULL,
    receipt_file_id TEXT,
    receipt_type TEXT DEFAULT 'photo',
    receipt_file_name TEXT,
    transaction_id TEXT,
    status TEXT NOT NULL DEFAULT 'قيد المراجعة',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_spec ON orders(specialization);

-- طرق الدفع
CREATE TABLE IF NOT EXISTS payment_methods (
    name TEXT PRIMARY KEY,
    details TEXT NOT NULL,
    image_file_id TEXT
);

-- المحظورون
CREATE TABLE IF NOT EXISTS banned_users (
    user_id INTEGER PRIMARY KEY,
    banned_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- الإعدادات
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- فترات الانتظار
CREATE TABLE IF NOT EXISTS user_cooldowns (
    user_id INTEGER PRIMARY KEY,
    last_order_time TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- سجل البث
CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    total_users INTEGER,
    success_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    started_at TEXT DEFAULT (datetime('now', 'localtime')),
    completed_at TEXT,
    status TEXT DEFAULT 'running'
);

-- سجل تغيير حالة الطلبات
CREATE TABLE IF NOT EXISTS order_status_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_key TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by INTEGER,
    changed_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (order_key) REFERENCES orders(order_key)
);

-- تقييمات الطلبات
CREATE TABLE IF NOT EXISTS order_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (order_key) REFERENCES orders(order_key),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_order_ratings_user_id ON order_ratings(user_id);

-- الإشعارات الفاشلة
CREATE TABLE IF NOT EXISTS failed_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_text TEXT NOT NULL,
    parse_mode TEXT DEFAULT 'HTML',
    reply_markup_json TEXT,
    notification_type TEXT NOT NULL,
    reference_id INTEGER,
    attempt_count INTEGER DEFAULT 1,
    max_attempts INTEGER DEFAULT 8,
    next_retry_at TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_failed_notifications_retry ON failed_notifications(next_retry_at);
"""

PRAGMA_OPTIMIZATIONS = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA cache_size = -10000;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;
"""


class DatabasePool:
    """مجمع اتصالات SQLite: كتابة واحدة + 5 قراءات متزامنة"""

    def __init__(self, db_path: str = DB_PATH, read_pool_size: int = 5):
        self.db_path = db_path
        self.read_pool_size = read_pool_size
        self._write_connection: Optional[aiosqlite.Connection] = None
        self._read_connections: list[aiosqlite.Connection] = []
        self._read_available: asyncio.Queue = None
        self._write_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self):
        if self._read_available is not None:
            return
        async with self._init_lock:
            if self._read_available is not None:
                return
            self._read_available = asyncio.Queue(maxsize=self.read_pool_size)
            for _ in range(self.read_pool_size):
                try:
                    conn = await aiosqlite.connect(self.db_path)
                    conn.row_factory = aiosqlite.Row
                    await conn.executescript(PRAGMA_OPTIMIZATIONS)
                    self._read_connections.append(conn)
                    await self._read_available.put(conn)
                except Exception as e:
                    logger.error(f"Failed to create read connection: {e}")
            logger.info(f"Database pool initialized: 1 write + {len(self._read_connections)} read connections")

    async def get_connection(self) -> aiosqlite.Connection:
        async with self._write_lock:
            if self._write_connection is None:
                try:
                    self._write_connection = await aiosqlite.connect(self.db_path)
                    self._write_connection.row_factory = aiosqlite.Row
                    await self._write_connection.executescript(PRAGMA_OPTIMIZATIONS)
                    logger.info("Database write connection established with PRAGMA optimizations")
                except Exception as e:
                    logger.error(f"Failed to create write connection: {e}")
                    raise
            return self._write_connection

    async def get_read_connection(self) -> aiosqlite.Connection:
        await self._ensure_initialized()
        return await self._read_available.get()

    async def release_read_connection(self, conn: aiosqlite.Connection):
        if self._read_available is not None:
            await self._read_available.put(conn)

    async def close(self):
        async with self._write_lock:
            if self._write_connection:
                try:
                    await self._write_connection.close()
                except Exception as e:
                    logger.warning(f"Error closing write connection: {e}")
                self._write_connection = None
        for conn in self._read_connections:
            try:
                await conn.close()
            except Exception as e:
                logger.warning(f"Error closing read connection: {e}")
        self._read_connections.clear()
        self._read_available = None


_db_pool: Optional[DatabasePool] = None


def get_db_pool() -> DatabasePool:
    global _db_pool
    if _db_pool is None:
        _db_pool = DatabasePool()
    return _db_pool


async def init_database(db_path: str = DB_PATH) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(PRAGMA_OPTIMIZATIONS)
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    logger.info(f"Database initialized with optimizations: {db_path}")
    return db


ORDER_TABLE_MIGRATIONS = [
    ("specialization", "ALTER TABLE orders ADD COLUMN specialization TEXT"),
    ("receipt_type", "ALTER TABLE orders ADD COLUMN receipt_type TEXT DEFAULT 'photo'"),
    ("receipt_file_name", "ALTER TABLE orders ADD COLUMN receipt_file_name TEXT"),
    ("transaction_id", "ALTER TABLE orders ADD COLUMN transaction_id TEXT"),
]


async def _ensure_columns_exist(db, table_name: str, required_migrations: list) -> bool:
    try:
        cursor = await db.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        await cursor.close()
        existing = {row['name'] for row in rows}
    except Exception as e:
        logger.error(f"Migration: Failed to get table info for '{table_name}': {e}")
        return False

    missing = [(c, sql) for c, sql in required_migrations if c not in existing]
    if not missing:
        return True

    ok = True
    for col, sql in missing:
        try:
            await db.execute(sql)
            await db.commit()
            logger.info(f"Migration: added column '{col}' to '{table_name}'")
        except Exception as e:
            if "duplicate column" in str(e).lower():
                continue
            logger.error(f"Migration: FAILED adding '{col}' to '{table_name}': {e}")
            ok = False
    return ok


async def migrate_database(db_path: str = DB_PATH) -> bool:
    """ترحيل آمن قابل للتشغيل المتكرر"""
    overall = True
    db = None
    try:
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        await db.executescript(PRAGMA_OPTIMIZATIONS)
        await db.executescript(SCHEMA_SQL)
        await db.commit()

        if not await _ensure_columns_exist(db, 'orders', ORDER_TABLE_MIGRATIONS):
            overall = False

        return overall
    except Exception as e:
        logger.error(f"Database migration error: {e}")
        return False
    finally:
        if db:
            try:
                await db.close()
            except Exception:
                pass
