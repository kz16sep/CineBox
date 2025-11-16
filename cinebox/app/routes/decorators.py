"""
Decorators for authentication and authorization
"""

from functools import wraps
from flask import session, redirect, url_for, flash


def login_required(f):
    """Decorator kiểm tra đăng nhập"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Vui lòng đăng nhập để truy cập trang này.", "error")
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator kiểm tra quyền admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Vui lòng đăng nhập để truy cập trang này.", "error")
            return redirect(url_for("main.login"))
        
        if session.get("role") != "Admin":
            flash("Bạn không có quyền truy cập trang này.", "error")
            return redirect(url_for("main.home"))
        
        return f(*args, **kwargs)
    return decorated_function

