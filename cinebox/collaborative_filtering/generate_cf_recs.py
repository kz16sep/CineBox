import os
import math
import datetime as dt
from typing import List, Tuple

import joblib
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

MODEL_PATH_ENV = "CF_MODEL_PATH"
TOP_N_ENV = "CF_TOP_N"
TTL_HOURS_ENV = "CF_TTL_HOURS"
DRY_RUN_ENV = "CF_DRY_RUN"
TARGET_USER_ENV = "CF_TARGET_USER"
DEBUG_PRED_ENV = "CF_DEBUG_PRED"

def get_engine():
    """
    Kết nối SQL Server qua pyodbc. Ưu tiên biến môi trường, fallback sang localhost.
    """
    # Ưu tiên chuỗi odbc_connect nếu có (full DSN)
    odbc_connect = os.getenv("DB_ODBC_CONNECT")
    if odbc_connect:
        connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_connect})
        return create_engine(connection_url, fast_executemany=True)

    # Hoặc dùng thành phần rời
    driver = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "1433")
    database = os.getenv("DB_NAME", "CineBoxDB")
    user = os.getenv("DB_USER", "sa")
    password = os.getenv("DB_PASSWORD", "sapassword")
    encrypt = os.getenv("DB_ENCRYPT", "yes")  # yes/no
    trust = os.getenv("DB_TRUST_CERT", "yes") # yes/no

    odbc_str = (
        f"DRIVER={driver};"
        f"SERVER={host},{port};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust};"
    )
    connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
    return create_engine(connection_url, fast_executemany=True)

def load_model(model_path: str):
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")
    return joblib.load(model_path)

def resolve_model_path(default_dir: str) -> str:
    env_path = os.getenv(MODEL_PATH_ENV)
    if env_path and os.path.isfile(env_path):
        return env_path
    for name in ("svd_cf_model.pkl"):
        cand = os.path.join(default_dir, name)
        if os.path.isfile(cand):
            return cand
    # Trả về đường dẫn mặc định (load_model sẽ báo l    ỗi rõ ràng nếu không tồn tại)
    return os.path.join(default_dir, "svd_cf_model.pkl")

def fetch_all_users(conn) -> List[int]:
    rows = conn.execute(
        text("SELECT userId FROM cine.[User] WHERE status = N'active'")
    ).fetchall()
    return [int(r[0]) for r in rows]

def fetch_candidate_movies_for_user(conn, user_id: int, recent_days: int = 90) -> List[int]:
    """
    Candidate = tất cả movie trừ:
      - đã rating
      - đã like
      - đã nằm watchlist
      - đã xem trong N ngày gần đây
    """
    rows = conn.execute(
        text("""
        WITH recent_view AS (
            SELECT vh.movieId
            FROM cine.ViewHistory vh
            WHERE vh.userId = :uid
              AND vh.startedAt >= DATEADD(day, -:recent_days, SYSUTCDATETIME())
            GROUP BY vh.movieId
        )
        SELECT m.movieId
        FROM cine.Movie AS m
        WHERE NOT EXISTS (SELECT 1 FROM cine.Rating    r  WHERE r.userId = :uid AND r.movieId = m.movieId)
          AND NOT EXISTS (SELECT 1 FROM cine.MovieLike l  WHERE l.userId = :uid AND l.movieId = m.movieId)
          AND NOT EXISTS (SELECT 1 FROM cine.Watchlist w  WHERE w.userId = :uid AND w.movieId = m.movieId)
          AND NOT EXISTS (SELECT 1 FROM recent_view    rv WHERE rv.movieId = m.movieId);
        """),
        {"uid": user_id, "recent_days": recent_days}
    ).fetchall()
    return [int(r[0]) for r in rows]

def write_recommendations(conn, user_id: int, recs: List[Tuple[int, float]], top_n: int, ttl_hours: int):
    # Xoá rec cũ của user cho đơn giản, tránh đụng UX_PersonalRecommendation
    conn.execute(text("DELETE FROM cine.PersonalRecommendation WHERE userId = :uid"), {"uid": user_id})

    expires_at = dt.datetime.utcnow() + dt.timedelta(hours=ttl_hours)
    generated_at = dt.datetime.utcnow()
    algo = "collaborative"

    payload = []
    for rank, (movie_id, score) in enumerate(recs[:top_n], start=1):
        payload.append({
            "userId": int(user_id),
            "movieId": int(movie_id),
            "score": float(score) if math.isfinite(score) else 0.0,
            "rank": int(rank),
            "algo": algo,
            "generatedAt": generated_at,
            "expiresAt": expires_at,
        })

    if payload:
        conn.execute(
            text("""
                INSERT INTO cine.PersonalRecommendation
                    (userId, movieId, score, rank, algo, generatedAt, expiresAt)
                VALUES
                    (:userId, :movieId, :score, :rank, :algo, :generatedAt, :expiresAt)
            """),
            payload  # bulk insert nhờ fast_executemany
        )

def score_user_candidates(
    model,
    user_id: int,
    candidate_movie_ids: List[int],
    debug: bool = False,
    debug_limit: int = 5,
) -> List[Tuple[int, float]]:
    """Score candidate movies for a user.

    If debug=True, print up to debug_limit raw prediction objects to help diagnose model.predict's return type.
    """
    scored: List[Tuple[int, float]] = []
    for idx, movie_id in enumerate(candidate_movie_ids):
        try:
            # Try common Surprise-style API first
            try:
                pred = model.predict(user_id, movie_id, clip=False)
            except TypeError:
                # Some models expect string ids or different signature
                pred = model.predict(str(user_id), str(movie_id), clip=False)

            # Debug print for first few predictions
            if debug and idx < debug_limit:
                try:
                    print(f"DEBUG pred[{idx}] for user={user_id}, movie={movie_id}: type={type(pred)}, repr={repr(pred)}")
                except Exception:
                    print(f"DEBUG pred[{idx}] for user={user_id}, movie={movie_id}: (failed to repr)")

            # Extract numeric estimate from common return types
            est = None
            if hasattr(pred, "est"):
                try:
                    est = float(pred.est)
                except Exception:
                    est = None
            elif isinstance(pred, (tuple, list)) and len(pred) >= 4:
                try:
                    est = float(pred[3])
                except Exception:
                    est = None
            else:
                try:
                    est = float(pred)
                except Exception:
                    est = None

            if est is None or not math.isfinite(est):
                est = 0.0
        except Exception as e:
            # Log the error so we can debug why predictions fail
            print(f"Prediction error for user {user_id}, movie {movie_id}: {e}")
            est = 0.0

        scored.append((movie_id, est))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

def generate_for_user(conn, model, user_id: int, top_n: int, ttl_hours: int):
    candidates = fetch_candidate_movies_for_user(conn, user_id)
    debug = os.getenv(DEBUG_PRED_ENV, "0").lower() in ("1", "true", "yes")
    recs = score_user_candidates(model, user_id, candidates, debug=debug)
    write_recommendations(conn, user_id, recs, top_n, ttl_hours)

def generate_for_user_dryrun(conn, model, user_id: int, top_n: int):
    candidates = fetch_candidate_movies_for_user(conn, user_id)
    debug = os.getenv(DEBUG_PRED_ENV, "0").lower() in ("1", "true", "yes")
    recs = score_user_candidates(model, user_id, candidates, debug=debug)
    return recs[:top_n]

def main():
    model_path = resolve_model_path(os.path.dirname(__file__))
    top_n = int(os.getenv(TOP_N_ENV, "50"))
    ttl_hours = int(os.getenv(TTL_HOURS_ENV, "24"))
    dry_run = os.getenv(DRY_RUN_ENV, "0").lower() in ("1", "true", "yes")
    target_user = os.getenv(TARGET_USER_ENV)

    engine = get_engine()
    model = load_model(model_path)

    with engine.begin() as conn:
        users = fetch_all_users(conn)
        if target_user:
            try:
                users = [int(target_user)]
            except ValueError:
                print(f"CF_TARGET_USER={target_user} is not a valid integer; ignoring and processing all users.")

        print(f"Loaded model from: {model_path}")
        print(f"Processing {len(users)} users (top_n={top_n}, ttl_hours={ttl_hours}, dry_run={dry_run})")

        for idx, user_id in enumerate(users, start=1):
            print(f"[{idx}/{len(users)}] Scoring user {user_id}...")
            if dry_run:
                top_recs = generate_for_user_dryrun(conn, model, user_id, top_n)
                print(f"Top {min(10, len(top_recs))} for user {user_id}: {top_recs[:10]}")
            else:
                generate_for_user(conn, model, user_id, top_n, ttl_hours)
                print(f"Wrote recommendations for user {user_id}")

if __name__ == "__main__":
    main()
