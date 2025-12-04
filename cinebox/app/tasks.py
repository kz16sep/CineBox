import threading
import time


def start_home_cache_warmers(app):
    """Start background threads to warm homepage caches."""
    if app.config.get("HOME_CACHE_WARMER_STARTED"):
        return

    interval = app.config.get("HOME_CACHE_REFRESH_INTERVAL", 300)

    def warm_homepage():
        while True:
            try:
                with app.app_context():
                    from .routes.movies import precompute_homepage_caches
                    precompute_homepage_caches()
            except Exception as exc:
                app.logger.error(f"Background cache warmer error: {exc}", exc_info=True)
            time.sleep(interval)

    thread = threading.Thread(
        target=warm_homepage,
        name="HomeCacheWarmer",
        daemon=True,
    )
    thread.start()
    app.config["HOME_CACHE_WARMER_STARTED"] = True

