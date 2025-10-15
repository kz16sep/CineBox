from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

odbc_str = (
    "DRIVER=ODBC Driver 17 for SQL Server;"
    "SERVER=localhost,1433;"
    "DATABASE=CineBoxDB;"     
    "UID=sa;"
    "PWD=sapassword;"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)
connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
engine = create_engine(connection_url, fast_executemany=True)

with engine.connect() as conn:
    print("Current DB:", conn.execute(text("SELECT DB_NAME()")).scalar_one())
    # kiểm tra CineBoxDB đã có bảng nào chưa
    rows = conn.execute(text("""
        SELECT TOP 5 s.name+'.'+t.name AS table_name
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
        ORDER BY t.create_date DESC
    """)).mappings().all()
    print("Recent tables:", [r["table_name"] for r in rows])
