#!/usr/bin/env python3
"""
Script để kiểm tra trạng thái retrain CF model
"""

from app import create_app
from app.routes.common import get_cf_state
from datetime import datetime

app = create_app()

with app.app_context():
    state = get_cf_state()
    
    print("=" * 60)
    print("TRẠNG THÁI RETRAIN CF MODEL")
    print("=" * 60)
    print()
    
    cf_dirty = state.get('cf_dirty', 'false')
    cf_last_retrain = state.get('cf_last_retrain', None)
    
    print(f"CF Dirty (cần retrain): {cf_dirty}")
    print(f"Lần retrain cuối: {cf_last_retrain if cf_last_retrain else 'Chưa có'}")
    
    if cf_last_retrain:
        try:
            last_dt = datetime.fromisoformat(cf_last_retrain)
            now = datetime.utcnow()
            diff = now - last_dt
            print(f"Thời gian từ lần retrain cuối: {diff}")
            
            from config import get_config
            config = get_config()
            interval_minutes = config.RETRAIN_INTERVAL_MINUTES
            print(f"Interval tối thiểu: {interval_minutes} phút")
            
            if diff.total_seconds() / 60 >= interval_minutes:
                print("✅ Đã đủ thời gian để retrain tiếp theo")
            else:
                remaining = interval_minutes - (diff.total_seconds() / 60)
                print(f"⏳ Còn {remaining:.1f} phút nữa mới có thể retrain")
        except Exception as e:
            print(f"Lỗi parse datetime: {e}")
    
    print()
    print("=" * 60)
    
    if cf_dirty == 'true':
        print("⚠️  Model đang được đánh dấu cần retrain")
        if cf_last_retrain:
            try:
                last_dt = datetime.fromisoformat(cf_last_retrain)
                now = datetime.utcnow()
                diff = now - last_dt
                from config import get_config
                config = get_config()
                interval_minutes = config.RETRAIN_INTERVAL_MINUTES
                
                if diff.total_seconds() / 60 >= interval_minutes:
                    print("✅ Background worker sẽ retrain trong vòng 60 giây tới")
                else:
                    remaining = interval_minutes - (diff.total_seconds() / 60)
                    print(f"⏳ Sẽ retrain sau {remaining:.1f} phút nữa")
            except:
                pass
    else:
        print("✅ Model không cần retrain (cf_dirty = false)")

