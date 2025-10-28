# Time Decay Feature - Implemented

## Overview

Time Decay đã được implement theo **Cách 2 (Post-processing)** - không cần retrain model.

## How It Works

1. **Get recommendations từ model** (như cũ)
2. **Lấy timestamp** của user interactions với mỗi movie
3. **Calculate time decay weight** dựa trên thời gian
4. **Boost score** cho phim user đã tương tác gần đây
5. **Re-sort và return** top recommendations

## Time Decay Formula

```python
# Exponential decay: weight = e^(-ln(2) * days_ago / half_life)
# Default half_life = 30 days

Timeline:
- 0 days:     100% weight (no decay)
- 15 days:    70% weight  
- 30 days:    50% weight (half-life)
- 60 days:    25% weight
- 90 days:    12.5% weight
```

## Configuration

Trong `enhanced_cf_recommender.py`:

```python
# Half-life parameter (có thể điều chỉnh)
half_life_days = 30  # Default 30 days

# Boost multiplier (có thể điều chỉnh)
boost_multiplier = 0.3  # Boost 0-30% dựa trên recency
```

## Usage

Không cần thay đổi code ở `app/routes.py`. Time decay tự động hoạt động khi:
- User xem recommendations
- Enhanced CF recommender được sử dụng

## Benefits

- ✅ Không cần retrain model
- ✅ Tự động ưu tiên tương tác gần đây
- ✅ Linh hoạt điều chỉnh parameters
- ✅ Dễ dàng A/B testing

## Files Modified

- `enhanced_cf_recommender.py` - Added time decay methods

## No Changes Needed

- `routes.py` - Vẫn hoạt động bình thường
- Model files - Không cần update
- Database - Không cần migration

