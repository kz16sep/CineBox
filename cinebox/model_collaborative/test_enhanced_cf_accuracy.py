#!/usr/bin/env python3
"""
Enhanced CF Evaluation (Implicit ALS artifacts)
- RMSE/MAE (tham khảo, quy về thang 1..5 qua sigmoid)
- Precision@K / Recall@K (quan trọng hơn với implicit)
"""

import os
import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error
import joblib  # dùng joblib thay pickle cho an toàn

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("cf_eval")

def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))

class EnhancedCFAccuracyTester:
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.artifacts = None
        self.model = None
        self.user2idx = None
        self.movie2idx = None
        self.inv_movie = None  # idx->movieId
        self.train_data: Optional[pd.DataFrame] = None
        self.test_data: Optional[pd.DataFrame] = None

    def load_model(self, model_path='cinebox/model_collaborative/enhanced_cf_model.pkl'):
        """Load artifacts: {'model','user2idx','movie2idx'}"""
        try:
            self.artifacts = joblib.load(model_path)
            self.model = self.artifacts["model"]
            self.user2idx = self.artifacts["user2idx"]  # dict userId->u_idx
            self.movie2idx = self.artifacts["movie2idx"]  # dict movieId->i_idx
            # inverse mapping idx->movieId
            self.inv_movie = {v: k for k, v in self.movie2idx.items()}
            print(f"Model loaded from {model_path}")
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            raise

    def prepare_test_data(self, test_size=0.2, random_state=42):
        """Chuẩn bị dữ liệu train/test từ bảng cine.Rating (cột 'rating')."""
        try:
            with self.db_engine.connect() as conn:
                ratings_df = pd.read_sql(text("""
                    SELECT userId, movieId, CAST(rating AS FLOAT) AS rating
                    FROM cine.Rating
                    WHERE rating IS NOT NULL
                """), conn)

            if ratings_df.empty:
                raise RuntimeError("No ratings found in cine.Rating")

            self.train_data, self.test_data = train_test_split(
                ratings_df, test_size=test_size, random_state=random_state, shuffle=True
            )
            print(f"Train: {len(self.train_data)} | Test: {len(self.test_data)}")
        except Exception as e:
            logger.error(f"Error preparing test data: {e}")
            raise

    def calculate_rmse(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """RMSE/MAE/MSE trên test_data (tham khảo)."""
        try:
            if self.model is None or self.test_data is None:
                raise ValueError("Model or test data not ready")

            preds, actuals = [], []
            uf = self.model.user_factors
            if uf is None:
                raise RuntimeError("Model has no user_factors (wrong artifact?)")
            vf = self.model.item_factors

            # chỉ giữ các cặp có mặt trong mapping
            mask = self.test_data["userId"].isin(self.user2idx.keys()) & \
                   self.test_data["movieId"].isin(self.movie2idx.keys())
            test_df = self.test_data.loc[mask].copy()
            if test_df.empty:
                return None, None, None

            for _, row in test_df.iterrows():
                uidx = self.user2idx[int(row.userId)]
                iidx = self.movie2idx[int(row.movieId)]
                dot = float(np.dot(uf[uidx], vf[iidx]))
                # map về 1..5 qua sigmoid
                p01 = sigmoid(dot)              # 0..1
                pr = 1.0 + 4.0 * p01            # 1..5
                preds.append(pr)
                actuals.append(float(row.rating))

            if not preds:
                return None, None, None

            mse = mean_squared_error(actuals, preds)
            rmse = float(np.sqrt(mse))
            mae = mean_absolute_error(actuals, preds)
            return rmse, mae, mse

        except Exception as e:
            logger.error(f"Error calculating RMSE: {e}")
            return None, None, None

    def _seen_items_in_train(self, user_id: int):
        """Danh sách item (theo index) đã thấy trong TRAIN để loại khỏi recommend."""
        if self.train_data is None:
            return []
        mids = self.train_data.loc[self.train_data["userId"] == user_id, "movieId"].tolist()
        idxs = [self.movie2idx[m] for m in mids if m in self.movie2idx]
        return idxs

    def calculate_precision_recall(self, k=10, sample_users=100) -> Tuple[Optional[float], Optional[float]]:
        """
        Precision@K, Recall@K:
        - Recommend cho user có trong mapping
        - Loại item đã thấy trong TRAIN (tránh leakage)
        - So sánh với item có mặt trong TEST của user đó
        """
        try:
            if self.model is None or self.test_data is None:
                raise ValueError("Model or test data not ready")

            precisions, recalls = [], []
            test_users = self.test_data["userId"].unique().tolist()

            count = 0
            for user_id in test_users:
                if user_id not in self.user2idx:
                    continue
                # ground-truth (tập phim của user trong TEST)
                user_test_movies = set(self.test_data.loc[self.test_data["userId"] == user_id, "movieId"].tolist())
                if not user_test_movies:
                    continue

                # loại item đã thấy trong TRAIN
                seen_item_indices = self._seen_items_in_train(user_id)

                # recommend top-(k + len(seen)) rồi lọc bớt
                uidx = self.user2idx[user_id]
                rec = self.model.recommend(
                    userid=uidx,
                    user_items=None,  # đã có user factor trong model
                    N=k + len(seen_item_indices),
                    filter_items=seen_item_indices,  # loại item đã thấy trong train
                    filter_already_liked_items=False
                )

                top_items = [i for (i, s) in rec][:k]
                recommended_movies = set(self.inv_movie[i] for i in top_items if i in self.inv_movie)

                inter = user_test_movies & recommended_movies
                if len(recommended_movies) > 0:
                    precision = len(inter) / len(recommended_movies)
                    recall = len(inter) / len(user_test_movies)
                    precisions.append(precision)
                    recalls.append(recall)

                count += 1
                if count >= sample_users:
                    break

            if not precisions:
                return None, None

            return float(np.mean(precisions)), float(np.mean(recalls))

        except Exception as e:
            logger.error(f"Error calculating precision/recall: {e}")
            return None, None

    def run_evaluation(self, k=10, sample_users=100):
        print("Starting Enhanced CF evaluation...")
        self.load_model()
        self.prepare_test_data()

        rmse, mae, mse = self.calculate_rmse()
        if rmse is not None:
            print(f"RMSE: {rmse:.4f}")
            print(f"MAE:  {mae:.4f}")
            print(f"MSE:  {mse:.4f}")
        else:
            print("Could not calculate RMSE/MAE (no overlap between test and model mappings)")

        p, r = self.calculate_precision_recall(k=k, sample_users=sample_users)
        if p is not None:
            print(f"Precision@{k}: {p:.4f}")
            print(f"Recall@{k}:    {r:.4f}")
            if (p + r) > 0:
                f1 = 2 * p * r / (p + r)
                print(f"F1@{k}:        {f1:.4f}")
        else:
            print("Could not calculate Precision/Recall (no eligible users)")

        print("Evaluation completed!")

def main():
    try:
        connection_string = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=localhost;"
            "DATABASE=CineBox;"
            "Trusted_Connection=yes;"
        )
        db_url = URL.create("mssql+pyodbc", query={"odbc_connect": connection_string})
        engine = create_engine(db_url, future=True)

        tester = EnhancedCFAccuracyTester(engine)
        tester.run_evaluation(k=10, sample_users=100)

    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    main()
