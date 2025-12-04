#!/usr/bin/env python3
"""
Script để cập nhật API sử dụng CommentRating thay vì cột likes trong Comment
Chạy sau khi đã xóa các cột likes, dislikes, likeCount khỏi bảng Comment
"""

import os
import re
import sys

# Bảo đảm có thể import app từ project root
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

def update_interactions_file():
    file_path = "app/routes/interactions.py"
    
    # Đọc file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. Cập nhật query lấy like count từ Comment sang CommentRating
    content = re.sub(
        r'SELECT likes FROM \[cine\]\.\[Comment\] WHERE commentId = :comment_id',
        'SELECT COUNT(*) FROM [cine].[CommentRating] WHERE commentId = :comment_id AND isLike = 1',
        content
    )
    
    # 2. Cập nhật query trong get_comments - sử dụng subquery hiệu quả hơn
    old_query = r'''# Lấy tất cả comment của phim kèm thông tin like
            comments = conn\.execute\(text\("""
                SELECT 
                    c\.commentId,
                    c\.content,
                    c\.createdAt,
                    \(SELECT COUNT\(\*\) FROM \[cine\]\.\[CommentRating\] cr2 WHERE cr2\.commentId = c\.commentId AND cr2\.isLike = 1\) as likeCount,
                    u\.email as user_email,
                    u\.avatarUrl,
                    u\.userId,
                    CASE WHEN cr\.userId IS NOT NULL THEN 1 ELSE 0 END as is_liked_by_current_user
                FROM \[cine\]\.\[Comment\] c
                JOIN \[cine\]\.\[User\] u ON c\.userId = u\.userId
                LEFT JOIN \[cine\]\.\[CommentRating\] cr ON c\.commentId = cr\.commentId AND cr\.userId = :current_user_id AND cr\.isLike = 1
                WHERE c\.movieId = :movie_id
                ORDER BY c\.createdAt ASC
            """\), \{"movie_id": movie_id, "current_user_id": user_id or 0\}'''
    
    new_query = '''# Lấy tất cả comment của phim kèm thông tin like (optimized)
            comments = conn.execute(text("""
                SELECT 
                    c.commentId,
                    c.content,
                    c.createdAt,
                    u.email as user_email,
                    u.avatarUrl,
                    u.userId,
                    ISNULL(like_counts.like_count, 0) as likeCount,
                    CASE WHEN user_likes.userId IS NOT NULL THEN 1 ELSE 0 END as is_liked_by_current_user
                FROM [cine].[Comment] c
                JOIN [cine].[User] u ON c.userId = u.userId
                LEFT JOIN (
                    SELECT commentId, COUNT(*) as like_count
                    FROM [cine].[CommentRating]
                    WHERE isLike = 1
                    GROUP BY commentId
                ) like_counts ON c.commentId = like_counts.commentId
                LEFT JOIN [cine].[CommentRating] user_likes ON c.commentId = user_likes.commentId 
                    AND user_likes.userId = :current_user_id AND user_likes.isLike = 1
                WHERE c.movieId = :movie_id
                ORDER BY c.createdAt ASC
            """), {"movie_id": movie_id, "current_user_id": user_id or 0}'''
    
    content = re.sub(old_query, new_query, content, flags=re.MULTILINE | re.DOTALL)
    
    # Ghi lại file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✅ Updated interactions.py successfully!")
    print("📝 Changes made:")
    print("   - Updated like count queries to use CommentRating")
    print("   - Optimized comment queries with JOINs instead of subqueries")

if __name__ == "__main__":
    update_interactions_file()
