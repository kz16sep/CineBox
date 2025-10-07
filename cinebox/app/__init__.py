
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

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

    from .routes import main_bp
    app.register_blueprint(main_bp)

    return app
