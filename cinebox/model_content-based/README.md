# 🎯 Content-Based Recommendation System - Hybrid Approach

## 📁 Folder Structure (Cleaned)

```
model_content-based/
├── improved_train.py              # Main training script (Hybrid Approach)
├── content_based_recommender.py   # Web application service class
├── simple_test.py                 # Simple testing script
├── test_web_app.py               # Web application testing
├── hybrid_model_backup.pkl       # Model backup file (50-200MB)
├── hybrid_approach_explained.md  # Detailed explanation
└── README.md                     # This file
```

## 🚀 Hybrid Approach Overview

### **Phase 1: Training**
- **Script:** `improved_train.py`
- **Dataset:** 10,000 movies from ml-32m
- **Features:** Genres (50%), Tags (25%), Title (15%), Year (5%), Popularity (3%), Rating (2%)
- **Output:** Model backup file + Database similarities

### **Phase 2: Database Storage**
- **Similarities:** Saved to `cine.MovieSimilarity` table
- **Records:** ~156 similarity pairs
- **Performance:** <0.01s query time

### **Phase 3: Web Application**
- **Service:** `content_based_recommender.py`
- **Testing:** `test_web_app.py`
- **Performance:** Real-time recommendations

## 📊 Current Status

✅ **Training Completed:** 10,000 movies processed
✅ **Database Ready:** 156 similarity pairs stored
✅ **Model Backup:** `hybrid_model_backup.pkl` created
✅ **Web App Ready:** Fast database queries

## 🎯 Performance Metrics

- **Query Time:** 0.0029 seconds
- **Average Similarity:** 0.918 (91.8%)
- **High Quality (≥0.9):** 78.2% of pairs
- **Database Records:** 156 similarity pairs
- **Model File Size:** ~50-200MB

## 🔧 Usage

### **Training (Hybrid Approach)**
```bash
python improved_train.py
```

### **Testing**
```bash
python simple_test.py
```

### **Web Application Testing**
```bash
python test_web_app.py
```

## 📈 Benefits

✅ **Model Quality:** Trained on 10k movies
✅ **Performance:** Fast database queries
✅ **Storage:** Optimized for production
✅ **Maintenance:** Easy SQL queries
✅ **Backup:** Model file for retraining
✅ **Scalability:** Handle multiple users

## 🎉 Ready for Production!

The system is ready for web application deployment with:
- Fast response times (<0.01s)
- High-quality recommendations
- Database-driven architecture
- Model backup for future retraining
