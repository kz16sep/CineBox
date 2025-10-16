#!/usr/bin/env python3
"""
Script để tạo user admin mặc định
Chạy: python create_admin.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from sqlalchemy import text

def create_admin_user():
    app = create_app()
    
    with app.app_context():
        try:
            with app.db_engine.begin() as conn:
                # Kiểm tra xem đã có admin chưa
                admin_exists = conn.execute(text("""
                    SELECT COUNT(*) FROM cine.[User] u
                    JOIN cine.Role r ON r.roleId = u.roleId
                    WHERE r.roleName = 'Admin'
                """)).scalar()
                
                if admin_exists > 0:
                    print("Admin user da ton tai!")
                    return
                
                # Lấy roleId của Admin
                admin_role_id = conn.execute(text("""
                    SELECT roleId FROM cine.Role WHERE roleName = 'Admin'
                """)).scalar()
                
                if not admin_role_id:
                    print("Khong tim thay role Admin!")
                    return
                
                # Tạo user admin
                conn.execute(text("""
                    INSERT INTO cine.[User] (email, status, roleId)
                    VALUES ('admin@cinebox.com', 'active', :roleId)
                """), {"roleId": admin_role_id})
                
                # Lấy userId vừa tạo
                admin_user_id = conn.execute(text("""
                    SELECT userId FROM cine.[User] WHERE email = 'admin@cinebox.com'
                """)).scalar()
                
                # Tạo account cho admin
                conn.execute(text("""
                    INSERT INTO cine.Account (username, passwordHash, userId)
                    VALUES ('admin', HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), 'admin123')), :userId)
                """), {"userId": admin_user_id})
                
                print("Da tao admin user thanh cong!")
                print("Email: admin@cinebox.com")
                print("Username: admin")
                print("Password: admin123")
                print("\nVui long doi mat khau sau khi dang nhap!")
                
        except Exception as e:
            print(f"Loi khi tao admin user: {e}")

if __name__ == "__main__":
    create_admin_user()
