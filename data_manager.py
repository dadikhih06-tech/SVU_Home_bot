"""
طبقة إدارة البيانات - مخطط التخصصات والكتل + سلة المشتريات
تخصص ← خدمة (سعر) ← كتلة ← مادة
"""
import json
import time
from datetime import datetime
from typing import Optional, List, Tuple, Set, Any

import aiosqlite

from config import logger, BACKUP_DIR
from database import DB_PATH, get_db_pool


class _PooledWriteConnection:
    __slots__ = ('_conn',)

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def close(self):
        pass


class DataManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._cache: dict = {}
        self._CACHE_TTL = {
            'maintenance_mode': 15,
            'prices': 60,
            'is_banned': 30,
        }

    # === الاتصالات ===

    async def _get_db(self) -> _PooledWriteConnection:
        pool = get_db_pool()
        return _PooledWriteConnection(await pool.get_connection())

    async def _fetch_one(self, query: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        pool = get_db_pool()
        conn = await pool.get_read_connection()
        try:
            async with conn.execute(query, params) as cursor:
                return await cursor.fetchone()
        finally:
            await pool.release_read_connection(conn)

    async def _fetch_all(self, query: str, params: tuple = ()) -> list:
        pool = get_db_pool()
        conn = await pool.get_read_connection()
        try:
            async with conn.execute(query, params) as cursor:
                return await cursor.fetchall()
        finally:
            await pool.release_read_connection(conn)

    # === Cache ===

    def _cache_get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.time() > expiry:
            del self._cache[key]
            return None
        return value

    def _cache_set(self, key: str, value: Any, ttl_key: str) -> None:
        ttl = self._CACHE_TTL.get(ttl_key, 30)
        self._cache[key] = (value, time.time() + ttl)

    def _cache_invalidate(self, key_pattern: str) -> None:
        for k in [k for k in self._cache if k.startswith(key_pattern)]:
            del self._cache[k]

    # ═══════════ التخصصات ═══════════

    async def get_specializations(self) -> List[str]:
        rows = await self._fetch_all("SELECT name FROM specializations ORDER BY rowid")
        return [r['name'] for r in rows]

    async def add_specialization(self, name: str) -> None:
        db = await self._get_db()
        try:
            await db.execute("INSERT OR IGNORE INTO specializations (name) VALUES (?)", (name,))
            await db.commit()
        finally:
            await db.close()

    async def delete_specialization(self, name: str) -> bool:
        db = await self._get_db()
        try:
            cursor = await db.execute("DELETE FROM specializations WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def rename_specialization(self, old_name: str, new_name: str) -> bool:
        db = await self._get_db()
        try:
            cursor = await db.execute(
                "UPDATE specializations SET name = ? WHERE name = ?", (new_name, old_name))
            if cursor.rowcount == 0:
                return False
            for t in ('spec_services', 'spec_blocks', 'block_courses'):
                await db.execute(f"UPDATE {t} SET spec_name = ? WHERE spec_name = ?",
                                 (new_name, old_name))
            await db.execute(
                "UPDATE orders SET specialization = ? WHERE specialization = ?",
                (new_name, old_name))
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"rename_specialization: {e}")
            return False
        finally:
            await db.close()

    # ═══════════ الخدمات ═══════════

    async def get_prices(self) -> dict:
        cached = self._cache_get('prices')
        if cached is not None:
            return cached
        rows = await self._fetch_all("SELECT name, price FROM services")
        result = {r['name']: r['price'] for r in rows}
        self._cache_set('prices', result, 'prices')
        return result

    async def set_service_price(self, name: str, price: int) -> None:
        db = await self._get_db()
        try:
            cursor = await db.execute("UPDATE services SET price = ? WHERE name = ?", (price, name))
            if cursor.rowcount == 0:
                await db.execute("INSERT INTO services (name, price) VALUES (?, ?)", (name, price))
            await db.commit()
        finally:
            await db.close()
        self._cache_invalidate('prices')

    async def delete_service(self, name: str) -> bool:
        db = await self._get_db()
        try:
            cursor = await db.execute("DELETE FROM services WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()
        self._cache_invalidate('prices')

    async def rename_service(self, old_name: str, new_name: str) -> bool:
        db = await self._get_db()
        try:
            cursor = await db.execute("UPDATE services SET name = ? WHERE name = ?",
                                      (new_name, old_name))
            if cursor.rowcount == 0:
                return False
            await db.execute("UPDATE spec_services SET service_name = ? WHERE service_name = ?",
                             (new_name, old_name))
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"rename_service: {e}")
            return False
        finally:
            await db.close()
        self._cache_invalidate('prices')

    async def get_active_services_for_spec(self, spec_name: str) -> List[str]:
        rows = await self._fetch_all(
            """SELECT s.service_name FROM spec_services s
               JOIN services sv ON sv.name = s.service_name
               WHERE s.spec_name = ? AND s.is_active = 1 ORDER BY s.rowid""", (spec_name,))
        return [r['service_name'] for r in rows]

    async def link_service_to_spec(self, spec_name: str, service_name: str,
                                   is_active: bool = True) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO spec_services (spec_name, service_name, is_active)
                   VALUES (?, ?, ?)""", (spec_name, service_name, 1 if is_active else 0))
            await db.commit()
        finally:
            await db.close()

    async def toggle_service_for_spec(self, spec_name: str, service_name: str) -> bool:
        db = await self._get_db()
        try:
            row = await self._fetch_one(
                "SELECT is_active FROM spec_services WHERE spec_name = ? AND service_name = ?",
                (spec_name, service_name))
            new_status = 0 if (row and row['is_active']) else 1
            await db.execute(
                """UPDATE spec_services SET is_active = ?
                   WHERE spec_name = ? AND service_name = ?""",
                (new_status, spec_name, service_name))
            await db.commit()
            return bool(new_status)
        finally:
            await db.close()

    # ═══════════ الكتل ═══════════

    async def get_blocks_for_spec(self, spec_name: str) -> List[str]:
        rows = await self._fetch_all(
            "SELECT block_name FROM spec_blocks WHERE spec_name = ? ORDER BY rowid", (spec_name,))
        return [r['block_name'] for r in rows]

    async def get_all_blocks_count(self) -> int:
        row = await self._fetch_one("SELECT COUNT(*) as c FROM spec_blocks")
        return row['c'] if row else 0

    async def add_block_to_spec(self, spec_name: str, block_name: str) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "INSERT OR IGNORE INTO spec_blocks (spec_name, block_name) VALUES (?, ?)",
                (spec_name, block_name))
            await db.commit()
        finally:
            await db.close()

    async def delete_block_from_spec(self, spec_name: str, block_name: str) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "DELETE FROM block_courses WHERE spec_name = ? AND block_name = ?",
                (spec_name, block_name))
            await db.execute(
                "DELETE FROM spec_blocks WHERE spec_name = ? AND block_name = ?",
                (spec_name, block_name))
            await db.commit()
        finally:
            await db.close()

    async def rename_block_in_spec(self, spec_name: str, old_name: str, new_name: str) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "UPDATE spec_blocks SET block_name = ? WHERE spec_name = ? AND block_name = ?",
                (new_name, spec_name, old_name))
            await db.execute(
                """UPDATE block_courses SET block_name = ?
                   WHERE spec_name = ? AND block_name = ?""",
                (new_name, spec_name, old_name))
            await db.commit()
        finally:
            await db.close()

    # ═══════════ المواد ═══════════

    async def get_master_courses(self) -> list:
        rows = await self._fetch_all("SELECT name FROM courses ORDER BY rowid")
        return [r['name'] for r in rows]

    async def add_course(self, course_name: str) -> None:
        db = await self._get_db()
        try:
            await db.execute("INSERT OR IGNORE INTO courses (name) VALUES (?)", (course_name,))
            await db.commit()
        finally:
            await db.close()

    async def get_courses_for_block(self, spec_name: str, block_name: str) -> list:
        rows = await self._fetch_all(
            """SELECT course_name FROM block_courses
               WHERE spec_name = ? AND block_name = ? AND is_active = 1 ORDER BY rowid""",
            (spec_name, block_name))
        return [r['course_name'] for r in rows]

    async def get_all_courses_for_block(self, spec_name: str, block_name: str) -> list:
        rows = await self._fetch_all(
            """SELECT course_name, is_active FROM block_courses
               WHERE spec_name = ? AND block_name = ? ORDER BY rowid""",
            (spec_name, block_name))
        return [dict(r) for r in rows]

    async def add_course_to_block(self, spec_name: str, block_name: str, course_name: str,
                                  is_active: bool = True) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO block_courses
                   (spec_name, block_name, course_name, is_active) VALUES (?, ?, ?, ?)""",
                (spec_name, block_name, course_name, 1 if is_active else 0))
            await db.commit()
        finally:
            await db.close()

    async def toggle_course_in_block(self, spec_name: str, block_name: str,
                                     course_name: str) -> bool:
        db = await self._get_db()
        try:
            row = await self._fetch_one(
                """SELECT is_active FROM block_courses
                   WHERE spec_name = ? AND block_name = ? AND course_name = ?""",
                (spec_name, block_name, course_name))
            if row:
                new_status = 0 if row['is_active'] else 1
                await db.execute(
                    """UPDATE block_courses SET is_active = ?
                       WHERE spec_name = ? AND block_name = ? AND course_name = ?""",
                    (new_status, spec_name, block_name, course_name))
            else:
                new_status = 1
                await db.execute(
                    """INSERT INTO block_courses
                       (spec_name, block_name, course_name, is_active) VALUES (?, ?, ?, 1)""",
                    (spec_name, block_name, course_name))
            await db.commit()
            return bool(new_status)
        finally:
            await db.close()

    async def remove_course_from_block(self, spec_name: str, block_name: str,
                                       course_name: str) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """DELETE FROM block_courses
                   WHERE spec_name = ? AND block_name = ? AND course_name = ?""",
                (spec_name, block_name, course_name))
            await db.commit()
        finally:
            await db.close()

    async def activate_all_courses_in_block(self, spec_name: str, block_name: str) -> int:
        db = await self._get_db()
        try:
            cursor = await db.execute(
                """UPDATE block_courses SET is_active = 1
                   WHERE spec_name = ? AND block_name = ? AND is_active = 0""",
                (spec_name, block_name))
            await db.commit()
            return cursor.rowcount
        finally:
            await db.close()

    async def search_courses_in_spec(self, spec_name: str, query: str) -> list:
        """بحث عبر كل كتل التخصص - يعيد [(block, course), ...]"""
        rows = await self._fetch_all(
            """SELECT block_name, course_name FROM block_courses
               WHERE spec_name = ? AND is_active = 1 AND course_name LIKE ?
               ORDER BY rowid""", (spec_name, f"%{query}%"))
        return [(r['block_name'], r['course_name']) for r in rows]

    # ═══════════ المستخدمون ═══════════

    async def get_all_users(self) -> dict:
        rows = await self._fetch_all("SELECT user_id, username, first_name FROM users WHERE is_active = 1")
        return {r['user_id']: {'username': r['username'], 'first_name': r['first_name']} for r in rows}

    async def get_all_user_ids(self) -> Set[int]:
        rows = await self._fetch_all("SELECT user_id FROM users WHERE is_active = 1")
        return {r['user_id'] for r in rows}

    async def get_user_info(self, user_id: int) -> Optional[dict]:
        row = await self._fetch_one(
            "SELECT username, first_name, is_active FROM users WHERE user_id = ?", (user_id,))
        return ({'username': row['username'], 'first_name': row['first_name'],
                 'is_active': row['is_active']} if row else None)

    async def add_user(self, user_id: int, info: dict) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """INSERT INTO users (user_id, username, first_name, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    is_active = 1""",
                (user_id, info.get('username'), info.get('first_name')))
            await db.commit()
        finally:
            await db.close()

    async def remove_user(self, user_id: int) -> bool:
        db = await self._get_db()
        try:
            cursor = await db.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"remove_user: {e}")
            return False
        finally:
            await db.close()

    async def get_user_count(self) -> int:
        row = await self._fetch_one("SELECT COUNT(*) as cnt FROM users WHERE is_active = 1")
        return row['cnt'] if row else 0

    async def get_user_profile(self, user_id: int) -> Optional[dict]:
        profile = {}
        row = await self._fetch_one(
            "SELECT username, first_name, created_at FROM users WHERE user_id = ?", (user_id,))
        if not row:
            return None
        profile['username'] = row['username']
        profile['first_name'] = row['first_name']
        profile['joined_at'] = row['created_at']

        rows = await self._fetch_all(
            "SELECT status, COUNT(*) as cnt FROM orders WHERE user_id = ? GROUP BY status",
            (user_id,))
        counts = {r['status']: r['cnt'] for r in rows}
        profile['total_orders'] = sum(counts.values())
        profile['pending_orders'] = counts.get('قيد المراجعة', 0)
        profile['approved_orders'] = counts.get('موافق عليه', 0)
        profile['completed_orders'] = counts.get('مكتمل', 0)
        profile['rejected_orders'] = counts.get('مرفوض', 0)

        row = await self._fetch_one(
            "SELECT COALESCE(SUM(total_price), 0) as total FROM orders WHERE user_id = ? AND status = 'مكتمل'",
            (user_id,))
        profile['total_spent'] = row['total'] if row else 0

        row = await self._fetch_one(
            "SELECT created_at FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,))
        profile['last_order_date'] = row['created_at'] if row else None
        return profile

    # ═══════════ الطلبات ═══════════

    @staticmethod
    def _order_from_row(row) -> dict:
        order = dict(row)
        order['courses'] = json.loads(order['courses']) if order['courses'] else []
        return order

    async def get_order(self, order_key: str) -> Optional[dict]:
        row = await self._fetch_one("SELECT * FROM orders WHERE order_key = ?", (order_key,))
        return self._order_from_row(row) if row else None

    async def get_all_orders(self) -> List[Tuple[str, dict]]:
        rows = await self._fetch_all("SELECT * FROM orders ORDER BY created_at DESC")
        return [(r['order_key'], self._order_from_row(r)) for r in rows]

    async def get_orders_by_status(self, status: str) -> List[Tuple[str, dict]]:
        rows = await self._fetch_all(
            "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC", (status,))
        return [(r['order_key'], self._order_from_row(r)) for r in rows]

    async def get_orders_by_user(self, user_id: int, limit: int = None) -> List[Tuple[str, dict]]:
        q = "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        rows = await self._fetch_all(q, (user_id,))
        return [(r['order_key'], self._order_from_row(r)) for r in rows]

    async def get_user_completed_orders_count(self, user_id: int) -> int:
        row = await self._fetch_one(
            "SELECT COUNT(*) as cnt FROM orders WHERE user_id = ? AND status = 'مكتمل'",
            (user_id,))
        return row['cnt'] if row else 0

    async def create_order(self, order_key: str, order_data: dict) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """INSERT INTO orders
                (order_key, user_id, full_name, username, specialization, service, courses,
                 payment_method, total_price, receipt_file_id, receipt_type,
                 receipt_file_name, transaction_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_key,
                    order_data.get('user_id'),
                    order_data.get('full_name'),
                    order_data.get('username'),
                    order_data.get('specialization'),
                    order_data.get('service'),
                    json.dumps(order_data.get('courses', []), ensure_ascii=False),
                    order_data.get('payment_method'),
                    order_data.get('total_price', 0),
                    order_data.get('receipt_file_id'),
                    order_data.get('receipt_type', 'photo'),
                    order_data.get('receipt_file_name'),
                    order_data.get('transaction_id'),
                    order_data.get('status', 'قيد المراجعة'),
                ))
            await db.commit()
        finally:
            await db.close()

    async def update_order_status(self, order_key: str, status: str, changed_by: int = None) -> bool:
        db = await self._get_db()
        try:
            row = await self._fetch_one("SELECT status FROM orders WHERE order_key = ?", (order_key,))
            if not row:
                return False
            await db.execute(
                "UPDATE orders SET status = ?, updated_at = datetime('now', 'localtime') WHERE order_key = ?",
                (status, order_key))
            await db.execute(
                "INSERT INTO order_status_log (order_key, old_status, new_status, changed_by) VALUES (?, ?, ?, ?)",
                (order_key, row['status'], status, changed_by))
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating order status: {e}")
            return False
        finally:
            await db.close()

    async def delete_orders_by_status(self, statuses: List[str]) -> int:
        db = await self._get_db()
        try:
            placeholders = ','.join('?' * len(statuses))
            cursor = await db.execute(
                f"DELETE FROM orders WHERE status IN ({placeholders})", statuses)
            await db.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Error deleting orders by status: {e}")
            return 0
        finally:
            await db.close()

    # ═══════════ الحظر ═══════════

    async def get_banned_users(self) -> Set[int]:
        rows = await self._fetch_all("SELECT user_id FROM banned_users")
        return {r['user_id'] for r in rows}

    async def is_banned(self, user_id: int) -> bool:
        cache_key = f'is_banned:{user_id}'
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        row = await self._fetch_one("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        result = row is not None
        self._cache_set(cache_key, result, 'is_banned')
        return result

    async def ban_user(self, user_id: int) -> None:
        db = await self._get_db()
        try:
            await db.execute("INSERT OR REPLACE INTO banned_users (user_id) VALUES (?)", (user_id,))
            await db.commit()
        finally:
            await db.close()
        self._cache_invalidate(f'is_banned:{user_id}')

    async def unban_user(self, user_id: int) -> None:
        db = await self._get_db()
        try:
            await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
            await db.commit()
        finally:
            await db.close()
        self._cache_invalidate(f'is_banned:{user_id}')

    # ═══════════ الإحصائيات ═══════════

    async def get_stats(self) -> dict:
        row = await self._fetch_one(
            """
            SELECT
                (SELECT COUNT(*) FROM users WHERE is_active = 1) AS total_users,
                (SELECT COUNT(*) FROM orders) AS total_orders,
                (SELECT COUNT(*) FROM orders WHERE status = 'قيد المراجعة') AS pending,
                (SELECT COUNT(*) FROM orders WHERE status = 'موافق عليه') AS approved,
                (SELECT COUNT(*) FROM orders WHERE status = 'مكتمل') AS completed,
                (SELECT COUNT(*) FROM orders WHERE status = 'مرفوض') AS rejected,
                (SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status = 'مكتمل') AS revenue
            """)
        if row:
            return {k: row[k] or 0 for k in row.keys()}
        return {'total_users': 0, 'total_orders': 0, 'pending': 0, 'approved': 0,
                'completed': 0, 'rejected': 0, 'revenue': 0}

    async def get_daily_revenue(self, days: int = 7) -> List[dict]:
        rows = await self._fetch_all(
            """SELECT DATE(created_at) as day, COUNT(*) as orders_count,
               COALESCE(SUM(total_price), 0) as revenue
               FROM orders WHERE status = 'مكتمل'
               AND created_at >= datetime('now', ? || ' days')
               GROUP BY DATE(created_at) ORDER BY day DESC""", (f'-{days}',))
        return [dict(r) for r in rows]

    async def get_popular_services(self, limit: int = 5) -> List[dict]:
        rows = await self._fetch_all(
            """SELECT service, COUNT(*) as count, COALESCE(SUM(total_price), 0) as revenue
               FROM orders WHERE status = 'مكتمل'
               GROUP BY service ORDER BY count DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]

    async def get_popular_specs(self, limit: int = 5) -> List[dict]:
        rows = await self._fetch_all(
            """SELECT COALESCE(specialization, 'غير محدد') as spec, COUNT(*) as count
               FROM orders WHERE status IN ('مكتمل', 'موافق عليه')
               GROUP BY specialization ORDER BY count DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]

    async def get_popular_courses(self, limit: int = 10) -> List[dict]:
        rows = await self._fetch_all(
            "SELECT courses FROM orders WHERE status IN ('مكتمل', 'موافق عليه')")
        counts = {}
        for r in rows:
            try:
                for c in json.loads(r['courses'] or '[]'):
                    counts[c] = counts.get(c, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{'course': c, 'count': n} for c, n in top]

    async def get_new_users_count(self, days: int = 7) -> int:
        row = await self._fetch_one(
            "SELECT COUNT(*) as cnt FROM users WHERE is_active = 1 AND created_at >= datetime('now', ? || ' days')",
            (f'-{days}',))
        return row['cnt'] if row else 0

    # ═══════════ طرق الدفع ═══════════

    async def get_payment_methods(self) -> dict:
        rows = await self._fetch_all("SELECT name, details, image_file_id FROM payment_methods")
        return {r['name']: {'details': r['details'], 'image_file_id': r['image_file_id']} for r in rows}

    async def get_payment_method(self, name: str) -> Optional[dict]:
        row = await self._fetch_one(
            "SELECT details, image_file_id FROM payment_methods WHERE name = ?", (name,))
        return ({'details': row['details'], 'image_file_id': row['image_file_id']} if row else None)

    async def add_payment_method(self, name: str, details: str, image_file_id: str = None) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO payment_methods (name, details, image_file_id) VALUES (?, ?, ?)",
                (name, details, image_file_id))
            await db.commit()
        finally:
            await db.close()

    async def update_payment_method(self, name: str, details: str, image_file_id: str = None) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "UPDATE payment_methods SET details = ?, image_file_id = ? WHERE name = ?",
                (details, image_file_id, name))
            await db.commit()
        finally:
            await db.close()

    async def delete_payment_method(self, name: str) -> bool:
        db = await self._get_db()
        try:
            cursor = await db.execute("DELETE FROM payment_methods WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    # ═══════════ الإعدادات ═══════════

    async def get_setting(self, key: str, default=None):
        row = await self._fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row['value'] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            await db.commit()
        finally:
            await db.close()

    async def get_maintenance_mode(self) -> bool:
        cached = self._cache_get('maintenance_mode')
        if cached is not None:
            return cached == '1'
        val = await self.get_setting('maintenance_mode', '0')
        self._cache_set('maintenance_mode', val, 'maintenance_mode')
        return val == '1'

    async def toggle_maintenance_mode(self) -> bool:
        current = await self.get_maintenance_mode()
        new_val = not current
        await self.set_setting('maintenance_mode', '1' if new_val else '0')
        self._cache_invalidate('maintenance_mode')
        return new_val

    # ═══════════ فترة الانتظار ═══════════

    async def get_last_order_time(self, user_id: int) -> Optional[datetime]:
        row = await self._fetch_one(
            "SELECT last_order_time FROM user_cooldowns WHERE user_id = ?", (user_id,))
        if row and row['last_order_time']:
            try:
                return datetime.fromisoformat(row['last_order_time'])
            except ValueError:
                return None
        return None

    async def set_last_order_time(self, user_id: int, time: datetime) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO user_cooldowns (user_id, last_order_time) VALUES (?, ?)",
                (user_id, time.isoformat()))
            await db.commit()
        finally:
            await db.close()

    # ═══════════ البث ═══════════

    async def set_active_broadcast_job(self, job_name: str) -> None:
        await self.set_setting('active_broadcast_job', job_name)

    async def clear_active_broadcast_job(self) -> Optional[str]:
        job_name = await self.get_setting('active_broadcast_job')
        if job_name:
            db = await self._get_db()
            try:
                await db.execute("DELETE FROM settings WHERE key = 'active_broadcast_job'")
                await db.commit()
            finally:
                await db.close()
        return job_name

    async def log_broadcast_start(self, admin_id: int, total_users: int) -> int:
        db = await self._get_db()
        try:
            cursor = await db.execute(
                "INSERT INTO broadcasts (admin_id, total_users, status) VALUES (?, ?, 'running')",
                (admin_id, total_users))
            await db.commit()
            return cursor.lastrowid
        finally:
            await db.close()

    async def log_broadcast_complete(self, broadcast_id: int, success: int, failed: int) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """UPDATE broadcasts SET success_count = ?, failed_count = ?,
                   completed_at = datetime('now', 'localtime'), status = 'completed'
                   WHERE id = ?""", (success, failed, broadcast_id))
            await db.commit()
        finally:
            await db.close()

    # ═══════════ النسخ الاحتياطي ═══════════

    async def send_backup_to_telegram(self, bot, chat_id: int) -> bool:
        import shutil
        from pathlib import Path as PathLib
        backup_path = None
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = BACKUP_DIR / f"backup_{timestamp}.db"
            shutil.copy2(self.db_path, backup_path)

            stats = await self.get_stats()
            caption = (
                f"📦 نسخة احتياطية تلقائية\n\n"
                f"📅 التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"👥 المستخدمون: {stats.get('total_users', 0)}\n"
                f"📋 الطلبات: {stats.get('total_orders', 0)}\n"
                f"⏳ قيد المراجعة: {stats.get('pending', 0)}\n"
                f"✅ مكتملة: {stats.get('completed', 0)}\n"
                f"💰 الإيرادات: {stats.get('revenue', 0):,} ل.س"
            )
            with open(backup_path, 'rb') as f:
                await bot.send_document(
                    chat_id=chat_id, document=f, caption=caption,
                    filename=f"svu_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            return True
        except Exception as e:
            logger.error(f"Failed to send backup to Telegram: {e}")
            return False
        finally:
            if backup_path:
                try:
                    PathLib(backup_path).unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                if BACKUP_DIR.exists() and not list(BACKUP_DIR.glob("*")):
                    BACKUP_DIR.rmdir()
            except Exception:
                pass

    # ═══════════ التقييمات ═══════════

    async def save_rating(self, order_key: str, user_id: int, rating: int) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                "INSERT OR IGNORE INTO order_ratings (order_key, user_id, rating) VALUES (?, ?, ?)",
                (order_key, user_id, rating))
            await db.commit()
        finally:
            await db.close()

    async def get_order_rating(self, order_key: str) -> Optional[dict]:
        row = await self._fetch_one("SELECT * FROM order_ratings WHERE order_key = ?", (order_key,))
        return dict(row) if row else None

    async def get_rating_stats(self) -> dict:
        row = await self._fetch_one(
            "SELECT COUNT(*) as total, COALESCE(AVG(rating), 0) as avg FROM order_ratings")
        rows = await self._fetch_all(
            "SELECT rating, COUNT(*) as cnt FROM order_ratings GROUP BY rating")
        return {
            'total_ratings': row['total'] if row else 0,
            'average_rating': round(row['avg'], 1) if row else 0,
            'distribution': {str(r['rating']): r['cnt'] for r in rows},
        }

    # ═══════════ الإشعارات الفاشلة ═══════════

    async def save_failed_notification(
        self, chat_id: int, message_text: str, parse_mode: str = "HTML",
        reply_markup_json: str = None, notification_type: str = "general",
        reference_id: int = None, attempt_count: int = 1, max_attempts: int = 8,
        next_retry_at: str = None, error_type: str = None, error_message: str = None
    ) -> int:
        db = await self._get_db()
        try:
            cursor = await db.execute(
                """INSERT INTO failed_notifications
                (chat_id, message_text, parse_mode, reply_markup_json, notification_type,
                 reference_id, attempt_count, max_attempts, next_retry_at, error_type, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, message_text, parse_mode, reply_markup_json, notification_type,
                 reference_id, attempt_count, max_attempts, next_retry_at, error_type, error_message))
            await db.commit()
            return cursor.lastrowid
        finally:
            await db.close()

    async def get_pending_retry_notifications(self, limit: int = 10) -> List[dict]:
        now = datetime.now().isoformat()
        rows = await self._fetch_all(
            """SELECT * FROM failed_notifications
               WHERE next_retry_at <= ? AND attempt_count < max_attempts
               ORDER BY next_retry_at ASC LIMIT ?""", (now, limit))
        return [dict(r) for r in rows]

    async def update_failed_notification_retry(
        self, notif_id: int, attempt_count: int, next_retry_at: str, error_type: str = None
    ) -> None:
        db = await self._get_db()
        try:
            await db.execute(
                """UPDATE failed_notifications
                   SET attempt_count = ?, next_retry_at = ?, error_type = ?,
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (attempt_count, next_retry_at, error_type, notif_id))
            await db.commit()
        finally:
            await db.close()

    async def delete_failed_notification(self, notif_id: int) -> None:
        db = await self._get_db()
        try:
            await db.execute("DELETE FROM failed_notifications WHERE id = ?", (notif_id,))
            await db.commit()
        finally:
            await db.close()


# --- Singleton ---
_dm_instance: Optional[DataManager] = None


def get_data_manager(context=None) -> DataManager:
    global _dm_instance
    if _dm_instance is None:
        _dm_instance = DataManager()
    return _dm_instance
