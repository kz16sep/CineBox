"""
Configuration Management
Quản lý tất cả configuration từ environment variables
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Base configuration class"""
    
    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or None
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=int(os.environ.get('SESSION_LIFETIME_HOURS', 2))
    )
    
    # SQL Server Configuration
    SQLSERVER_DRIVER = os.environ.get('SQLSERVER_DRIVER', 'ODBC Driver 17 for SQL Server')
    SQLSERVER_SERVER = os.environ.get('SQLSERVER_SERVER', 'localhost,1433')
    SQLSERVER_DB = os.environ.get('SQLSERVER_DB', 'CineBoxDB')
    SQLSERVER_UID = os.environ.get('SQLSERVER_UID', 'sa')
    SQLSERVER_PWD = os.environ.get('SQLSERVER_PWD', 'sapassword')
    SQL_ENCRYPT = os.environ.get('SQL_ENCRYPT', 'yes')
    SQL_TRUST_CERT = os.environ.get('SQL_TRUST_CERT', 'yes')
    
    # Database Connection Pool Configuration
    DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', 10))
    DB_MAX_OVERFLOW = int(os.environ.get('DB_MAX_OVERFLOW', 20))
    DB_POOL_TIMEOUT = int(os.environ.get('DB_POOL_TIMEOUT', 30))
    DB_POOL_RECYCLE = int(os.environ.get('DB_POOL_RECYCLE', 3600))
    DB_ECHO = os.environ.get('DB_ECHO', 'False').lower() == 'true'
    
    # Application Configuration
    RETRAIN_INTERVAL_MINUTES = int(os.environ.get('RETRAIN_INTERVAL_MINUTES', 30))
    WORKER_BASE_URL = os.environ.get('WORKER_BASE_URL', 'http://127.0.0.1:5000')
    
    # Environment
    ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development')


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'
    FLASK_HOST = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.environ.get('FLASK_PORT', 5000))


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    FLASK_HOST = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.environ.get('FLASK_PORT', 5000))
    
    # Production overrides
    DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', 20))  # Larger pool for production
    DB_MAX_OVERFLOW = int(os.environ.get('DB_MAX_OVERFLOW', 40))


class StagingConfig(Config):
    """Staging configuration"""
    DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
    FLASK_HOST = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.environ.get('FLASK_PORT', 5000))


# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'staging': StagingConfig,
    'production': ProductionConfig,
}


def get_config():
    """Get configuration based on ENVIRONMENT variable"""
    env = os.environ.get('ENVIRONMENT', 'development')
    return config.get(env, DevelopmentConfig)

