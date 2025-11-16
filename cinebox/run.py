from app import create_app
import threading
import time
import os
from datetime import datetime, timedelta
from flask import current_app
from config import get_config

# Load configuration
config = get_config()
RETRAIN_INTERVAL_MINUTES = config.RETRAIN_INTERVAL_MINUTES


app = create_app()


def background_retrain_worker(app):
    with app.app_context():
        while True:
            try:
                # Lazy import inside app context
                from app.routes.common import get_cf_state, clear_cf_dirty_and_set_last
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
                    # Call retrain endpoint via HTTP to avoid circular imports
                    import requests
                    try:
                        base = config.WORKER_BASE_URL if hasattr(config, 'WORKER_BASE_URL') else 'http://localhost:5000'
                        secret = os.environ.get('INTERNAL_RETRAIN_SECRET', 'internal-retrain-secret-key-change-in-production')
                        current_app.logger.info(f"Starting background retrain via {base}/api/retrain_cf_model_internal")
                        resp = requests.post(
                            f"{base}/api/retrain_cf_model_internal",
                            json={"secret": secret},
                            headers={"X-Internal-Secret": secret},
                            timeout=300  # 5 phút timeout
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if data and data.get('success'):
                                clear_cf_dirty_and_set_last(datetime.utcnow().isoformat())
                                current_app.logger.info("Background retrain completed successfully")
                            else:
                                current_app.logger.warning(f"Background retrain failed: {data.get('message') if data else 'Unknown error'}")
                        elif resp.status_code == 401:
                            current_app.logger.error("Background retrain unauthorized - check INTERNAL_RETRAIN_SECRET")
                        else:
                            current_app.logger.warning(f"Background retrain HTTP error: {resp.status_code}")
                            try:
                                error_data = resp.json()
                                current_app.logger.warning(f"Error details: {error_data.get('message', 'Unknown')}")
                            except:
                                current_app.logger.warning(f"Error response: {resp.text[:200]}")
                    except requests.exceptions.Timeout:
                        current_app.logger.error("Background retrain timeout (exceeded 5 minutes)")
                    except Exception as e:
                        current_app.logger.error(f"Background retrain error: {e}", exc_info=True)
            except Exception:
                pass
            time.sleep(60)


threading.Thread(target=background_retrain_worker, args=(app,), daemon=True).start()


if __name__ == "__main__":
    # Use configuration from environment variables
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.DEBUG
    )


