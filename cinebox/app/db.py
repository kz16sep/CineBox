"""
Database Connection Management Module
Cung cấp các helper functions để quản lý database connections an toàn
"""

from contextlib import contextmanager
from sqlalchemy import text
from sqlalchemy.engine import Engine
from flask import current_app
import logging

logger = logging.getLogger(__name__)


@contextmanager
def get_db_connection(db_engine=None, autocommit=False):
    """
    Context manager để quản lý database connection an toàn.
    Tự động commit/rollback và đóng connection.
    
    Args:
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
        autocommit: Nếu True, tự động commit sau mỗi statement (không khuyến khích)
    
    Usage:
        with get_db_connection() as conn:
            result = conn.execute(text("SELECT * FROM ..."))
            # Tự động commit khi exit context (nếu không có exception)
    """
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    # Sử dụng begin() để tự động quản lý transaction
    if autocommit:
        # Autocommit mode - không dùng transaction
        conn = db_engine.connect()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error (autocommit mode): {e}", exc_info=True)
            raise
        finally:
            conn.close()
    else:
        # Transaction mode - sử dụng begin() context manager
        with db_engine.begin() as conn:
            try:
                yield conn
            except Exception as e:
                logger.error(f"Database error: {e}", exc_info=True)
                raise


@contextmanager
def get_db_transaction(db_engine=None):
    """
    Context manager cho transaction phức tạp với nhiều operations.
    Tự động rollback nếu có exception.
    
    Usage:
        with get_db_transaction() as conn:
            conn.execute(text("INSERT INTO ..."))
            conn.execute(text("UPDATE ..."))
            # Tự động commit nếu không có exception
    """
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    with db_engine.begin() as conn:
        try:
            yield conn
        except Exception as e:
            logger.error(f"Transaction error: {e}", exc_info=True)
            raise


def execute_query(query: str, params: dict = None, db_engine=None, fetch_one=False, fetch_all=False):
    """
    Helper function để execute query và return results.
    
    Args:
        query: SQL query string
        params: Query parameters dict
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
        fetch_one: Nếu True, return single row
        fetch_all: Nếu True, return all rows
    
    Returns:
        - fetch_one=True: Single row (dict) hoặc None
        - fetch_all=True: List of rows (dicts)
        - Mặc định: Result object
    """
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    with get_db_connection(db_engine) as conn:
        result = conn.execute(text(query), params or {})
        
        if fetch_one:
            row = result.fetchone()
            return dict(row._mapping) if row else None
        elif fetch_all:
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]
        else:
            return result


def execute_many_queries(queries: list, db_engine=None):
    """
    Execute nhiều queries trong một transaction.
    
    Args:
        queries: List of tuples (query_string, params_dict)
        db_engine: SQLAlchemy engine
    
    Returns:
        List of results
    """
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    results = []
    with get_db_transaction(db_engine) as conn:
        for query, params in queries:
            result = conn.execute(text(query), params or {})
            results.append(result)
    return results


def get_pool_status(db_engine=None):
    """
    Lấy thông tin về connection pool status.
    
    Returns:
        dict: Pool status information
    """
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    pool = db_engine.pool
    return {
        'size': pool.size(),
        'checked_in': pool.checkedin(),
        'checked_out': pool.checkedout(),
        'overflow': pool.overflow(),
        'invalid': pool.invalid(),
    }

