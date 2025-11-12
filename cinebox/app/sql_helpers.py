"""
SQL Helper Functions
Cung cấp các helper functions để tạo SQL queries an toàn, tránh SQL injection
"""

from typing import Union
import logging

logger = logging.getLogger(__name__)


def validate_limit(limit: Union[int, str], max_limit: int = 1000, default: int = 10) -> int:
    """
    Validate và sanitize limit parameter để tránh SQL injection.
    
    Args:
        limit: Limit value (có thể là int hoặc str)
        max_limit: Giá trị tối đa cho phép (default: 1000)
        default: Giá trị mặc định nếu limit không hợp lệ (default: 10)
    
    Returns:
        int: Limit value đã được validate và sanitize
    """
    try:
        # Convert to int nếu là string
        if isinstance(limit, str):
            limit = int(limit)
        
        # Validate là integer
        if not isinstance(limit, int):
            logger.warning(f"Invalid limit type: {type(limit)}, using default: {default}")
            return default
        
        # Validate range
        if limit < 1:
            logger.warning(f"Limit too small: {limit}, using default: {default}")
            return default
        
        if limit > max_limit:
            logger.warning(f"Limit too large: {limit}, capping at max_limit: {max_limit}")
            return max_limit
        
        return limit
        
    except (ValueError, TypeError) as e:
        logger.warning(f"Error validating limit: {e}, using default: {default}")
        return default


def validate_table_name(table_name: str, allowed_tables: list = None) -> str:
    """
    Validate table name để tránh SQL injection.
    Chỉ cho phép các table names trong whitelist.
    
    Args:
        table_name: Table name cần validate
        allowed_tables: List các table names được phép (nếu None, dùng default list)
    
    Returns:
        str: Table name đã được validate (hoặc raise ValueError nếu không hợp lệ)
    
    Raises:
        ValueError: Nếu table name không có trong whitelist
    """
    if allowed_tables is None:
        # Default whitelist cho cine schema
        allowed_tables = [
            'Movie', 'User', 'Account', 'Rating', 'Favorite', 'Watchlist',
            'ViewHistory', 'Comment', 'Genre', 'MovieGenre', 'MovieSimilarity',
            'Role', 'PersonalRecommendation', 'ColdStartRecommendations',
            'AppState'
        ]
    
    # Remove whitespace và convert to title case
    table_name = table_name.strip()
    
    # Check if in whitelist
    if table_name not in allowed_tables:
        raise ValueError(f"Table name '{table_name}' is not in allowed list: {allowed_tables}")
    
    return table_name


def safe_top_clause(limit: Union[int, str], max_limit: int = 1000) -> str:
    """
    Tạo TOP clause an toàn cho SQL Server.
    
    Args:
        limit: Limit value
        max_limit: Giá trị tối đa cho phép
    
    Returns:
        str: TOP clause string (ví dụ: "TOP 10")
    """
    validated_limit = validate_limit(limit, max_limit=max_limit)
    return f"TOP {validated_limit}"


def safe_table_name(table_name: str, schema: str = 'cine') -> str:
    """
    Tạo table name an toàn với schema prefix.
    
    Args:
        table_name: Table name (sẽ được validate)
        schema: Schema name (default: 'cine')
    
    Returns:
        str: Safe table name với schema (ví dụ: "cine.Movie")
    """
    validated_table = validate_table_name(table_name)
    return f"{schema}.{validated_table}"

