import os
import secrets
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy import text
from config import get_config

def create_app():
    app = Flask(__name__)
    
    # Load configuration from environment variables
    config = get_config()
    
    # Generate a secure random SECRET_KEY if not set
    secret_key = config.SECRET_KEY
    if not secret_key:
        secret_key = secrets.token_hex(32)
        # Warn if using generated key (should set SECRET_KEY in .env)
        import warnings
        warnings.warn(
            "SECRET_KEY not set in environment variables. "
            "Using generated key (will change on restart). "
            "Set SECRET_KEY in .env file for production!",
            UserWarning
        )
    
    app.config.from_mapping(
        SECRET_KEY=secret_key,
        PERMANENT_SESSION_LIFETIME=config.PERMANENT_SESSION_LIFETIME,
        SQLSERVER_DRIVER=config.SQLSERVER_DRIVER,
        SQLSERVER_SERVER=config.SQLSERVER_SERVER,
        SQLSERVER_DB=config.SQLSERVER_DB,
        SQLSERVER_UID=config.SQLSERVER_UID,
        SQLSERVER_PWD=config.SQLSERVER_PWD,
        SQL_ENCRYPT=config.SQL_ENCRYPT,
        SQL_TRUST_CERT=config.SQL_TRUST_CERT,
        HOME_CACHE_REFRESH_INTERVAL=300,
    )

    odbc_str = (
        f"DRIVER={app.config['SQLSERVER_DRIVER']};"
        f"SERVER={app.config['SQLSERVER_SERVER']};"
        f"DATABASE={app.config['SQLSERVER_DB']};"
        f"UID={app.config['SQLSERVER_UID']};"
        f"PWD={app.config['SQLSERVER_PWD']};"
        f"Encrypt={app.config['SQL_ENCRYPT']};"
        f"TrustServerCertificate={app.config['SQL_TRUST_CERT']};"
    )
    connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
    
    # Connection Pool Configuration
    # pool_size: Số connections giữ trong pool
    # max_overflow: Số connections có thể tạo thêm khi cần
    # pool_timeout: Thời gian chờ connection từ pool (seconds)
    # pool_recycle: Thời gian recycle connection để tránh stale connections (seconds)
    # pool_pre_ping: Kiểm tra connection trước khi sử dụng (tránh stale connections)
    # fast_executemany: Tối ưu cho bulk operations
    engine = create_engine(
        connection_url,
        pool_size=config.DB_POOL_SIZE,
        max_overflow=config.DB_MAX_OVERFLOW,
        pool_timeout=config.DB_POOL_TIMEOUT,
        pool_recycle=config.DB_POOL_RECYCLE,
        pool_pre_ping=True,  # Kiểm tra connection trước khi dùng
        fast_executemany=True,  # Tối ưu bulk operations
        echo=config.DB_ECHO  # Log SQL queries (chỉ bật khi debug)
    )

    app.db_engine = engine

    # Seed roles and admin account if missing
    with app.db_engine.begin() as conn:
        # roles
        conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM cine.Role WHERE roleName = N'Admin')
                INSERT INTO cine.Role(roleId, roleName, description) VALUES (1, N'Admin', N'Quản trị');
            IF NOT EXISTS (SELECT 1 FROM cine.Role WHERE roleName = N'User')
                INSERT INTO cine.Role(roleId, roleName, description) VALUES (2, N'User', N'Người dùng');
        """))
        # admin user and account (password: admin123)
        conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM cine.[User] WHERE email = N'admin@cinebox.local')
            BEGIN
                DECLARE @rid INT = (SELECT roleId FROM cine.Role WHERE roleName=N'Admin');
                INSERT INTO cine.[User](userId, email, roleId) VALUES (1, N'admin@cinebox.local', @rid);
                DECLARE @uid BIGINT = SCOPE_IDENTITY();
                INSERT INTO cine.Account(accountId, username, passwordHash, userId)
                VALUES (1, N'admin', HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), N'admin123')), @uid);
            END
        """))

    from .routes import main_bp, init_recommenders
    app.register_blueprint(main_bp)
    
    # Initialize recommenders after app context is available
    with app.app_context():
        init_recommenders()
        from .tasks import start_home_cache_warmers
        start_home_cache_warmers(app)

    return app
