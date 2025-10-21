from app import create_app
import threading
import time
from datetime import datetime, timedelta
from flask import current_app

RETRAIN_INTERVAL_MINUTES = 30


app = create_app()


def background_retrain_worker(app):
    with app.app_context():
        while True:
            try:
                # Lazy import inside app context
                from app.routes import get_cf_state, clear_cf_dirty_and_set_last
                from flask import current_app
                state = get_cf_state()
                dirty = (state.get('cf_dirty') == 'true')
                last = state.get('cf_last_retrain')
                allow = True
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last)
                        allow = datetime.utcnow() - last_dt >= timedelta(minutes=RETRAIN_INTERVAL_MINUTES)
                    except Exception:
                        allow = True
                if dirty and allow:
                    # Call retrain endpoint logic via requests to reuse existing code
                    import requests
                    try:
                        base = 'http://127.0.0.1:5000'
                        resp = requests.get(f"{base}/api/retrain_cf_model", timeout=120)
                        if resp.status_code == 200 and resp.json().get('success'):
                            clear_cf_dirty_and_set_last(datetime.utcnow().isoformat())
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(60)


threading.Thread(target=background_retrain_worker, args=(app,), daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


