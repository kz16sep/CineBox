"""
Authentication routes: login, register, logout
"""

import re
from flask import render_template, request, redirect, url_for, session, current_app
from sqlalchemy import text
from . import main_bp
from .decorators import login_required


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login route"""
    success = request.args.get('success', '')
    error = None
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        current_app.logger.info(f"Login attempt: username='{username}'")
        
        with current_app.db_engine.connect() as conn:
            # Query kiểm tra đăng nhập với trạng thái user
            test_query = text("""
                SELECT u.userId, u.email, u.status, r.roleName
                FROM cine.Account a
                JOIN cine.[User] u ON u.userId = a.userId
                JOIN cine.Role r ON r.roleId = u.roleId
                WHERE (
                    a.username = :u OR u.email = :u
                ) AND a.passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p))
            """)
            
            try:
                result = conn.execute(test_query, {"u": username, "p": password})
                rows = result.fetchall()
                
                if rows:
                    row = rows[0]
                    current_app.logger.info(f"Found user: ID={row[0]}, Email={row[1]}, Status={row[2]}, Role={row[3]}")
                    
                    # Kiểm tra trạng thái user
                    if row[2] != "active":
                        current_app.logger.warning(f"User account is {row[2]}, login blocked")
                        error = "Tài khoản của bạn đã bị chặn. Vui lòng liên hệ quản trị viên."
                    else:
                        session["user_id"] = int(row[0])
                        session["role"] = row[3]
                        session["username"] = username
                        session["email"] = row[1]
                        current_app.logger.info(f"Session set: user_id={session['user_id']}, role={session['role']}")
                        return redirect(url_for("main.home"))
                else:
                    current_app.logger.warning("No user found with these credentials")
                    error = "Sai tài khoản hoặc mật khẩu"
            except Exception as e:
                current_app.logger.error(f"Database error: {e}")
                error = f"Lỗi database: {str(e)}"
    
    return render_template("login.html", error=error, success=success)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    """Register route"""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        
        # Validation
        errors = []
        
        # Name validation
        if not name:
            errors.append("Vui lòng nhập user name.")
        else:
            if len(name) < 1:
                errors.append("User name ít nhất phải có 1 ký tự và không chứa ký tự đặc biệt.")
            elif len(name) > 20:
                errors.append("User name không được quá 20 ký tự.")
            else:
                name_pattern = r'^[a-zA-Z0-9\u00C0-\u017F\u1E00-\u1EFF\u0100-\u017F\u0180-\u024F\s]+$'
                if not re.match(name_pattern, name):
                    errors.append("User name ít nhất phải có 1 ký tự và không chứa ký tự đặc biệt.")
        
        # Email validation
        if not email:
            errors.append("Vui lòng nhập email.")
        else:
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                errors.append("Email không hợp lệ.")
        
        # Password strength validation
        if not password:
            errors.append("Vui lòng nhập mật khẩu.")
        else:
            if len(password) < 8:
                errors.append("Mật khẩu phải có ít nhất 8 ký tự.")
            elif len(password) > 20:
                errors.append("Mật khẩu không được quá 20 ký tự.")
            if not re.search(r'[A-Z]', password):
                errors.append("Mật khẩu phải chứa ít nhất một chữ in hoa.")
            if not re.search(r'[a-z]', password):
                errors.append("Mật khẩu phải chứa ít nhất một chữ thường.")
            if not re.search(r'[0-9]', password):
                errors.append("Mật khẩu phải chứa ít nhất một số.")
        
        # If there are validation errors, return them
        if errors:
            return render_template("register.html", error="; ".join(errors))
        
        # Check email duplicates
        try:
            with current_app.db_engine.connect() as conn:
                existing_email = conn.execute(text("""
                    SELECT 1 FROM cine.[User] WHERE email = :email
                """), {"email": email}).scalar()
                
                if existing_email:
                    return render_template("register.html", error="Email này đã được sử dụng. Vui lòng đăng nhập hoặc dùng email khác.")
        except Exception as check_error:
            current_app.logger.error(f"Error checking duplicates: {check_error}")
        
        try:
            current_app.logger.info(f"Starting registration for email: {email}")
            with current_app.db_engine.begin() as conn:
                # Get role_id for User
                role_id = conn.execute(text("SELECT roleId FROM cine.Role WHERE roleName=N'User'")).scalar()
                
                if role_id is None:
                    max_role_id = conn.execute(text("SELECT ISNULL(MAX(roleId), 0) FROM cine.Role")).scalar()
                    role_id = max_role_id + 1
                    conn.execute(text("INSERT INTO cine.Role(roleId, roleName, description) VALUES (:roleId, N'User', N'Người dùng')"), {"roleId": role_id})
                
                # Get next available userId
                max_user_id = conn.execute(text("SELECT ISNULL(MAX(userId), 0) FROM cine.[User]")).scalar()
                user_id = max_user_id + 1
                
                # Insert user
                conn.execute(text("""
                    INSERT INTO cine.[User](userId, email, avatarUrl, roleId) 
                    VALUES (:userId, :email, NULL, :roleId)
                """), {"userId": user_id, "email": email, "roleId": role_id})
                
                # Get next available accountId
                max_account_id = conn.execute(text("SELECT ISNULL(MAX(accountId), 0) FROM cine.[Account]")).scalar()
                account_id = max_account_id + 1
                
                # Insert account
                conn.execute(text("""
                    INSERT INTO cine.[Account](accountId, username, passwordHash, userId) 
                    VALUES (:accountId, :u, HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p)), :uid)
                """), {"accountId": account_id, "u": name, "p": password, "uid": user_id})
                
                current_app.logger.info(f"Registration completed successfully with username: {name}")
            
            # Redirect to login with success message
            return redirect(url_for("main.login", success="Đăng ký thành công! Bạn có thể đăng nhập ngay."))
        except Exception as ex:
            current_app.logger.error(f"Registration error: {str(ex)}")
            return render_template("register.html", error=f"Không thể đăng ký: {str(ex)}")
    
    return render_template("register.html")


@main_bp.route("/logout")
def logout():
    """Logout route"""
    session.clear()
    return redirect(url_for("main.home"))

