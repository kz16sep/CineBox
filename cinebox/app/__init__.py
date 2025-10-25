
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy import text

def create_app():
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY="change-this",
        SQLSERVER_DRIVER="ODBC Driver 17 for SQL Server",
        SQLSERVER_SERVER="localhost,1433",   # hoặc "127.0.0.1,1433" hoặc "HOST\INSTANCE"
        SQLSERVER_DB="CineBoxDB",
        SQLSERVER_UID="sa",                  # dùng sa theo yêu cầu
        SQLSERVER_PWD="sapassword",
        SQL_ENCRYPT="yes",
        SQL_TRUST_CERT="yes",
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
    engine = create_engine(connection_url, pool_pre_ping=True, fast_executemany=True)

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

    return app
