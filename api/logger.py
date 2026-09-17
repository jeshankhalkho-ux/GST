# API Monitor Logger — fails silently, never crashes the app
import time

LOG_ENDPOINT = 'https://api-monitor-jeshankhalkho-ux.vercel.app'

try:
    import requests as _req
except ImportError:
    _req = None

try:
    from flask import request as _flask_request
except ImportError:
    _flask_request = None

class _Logger:
    def __init__(self, endpoint, api_name, category):
        self.endpoint = endpoint
        self.api_name = api_name
        self.category = category
        self._start = 0

    def before_request(self):
        try:
            self._start = time.time()
        except:
            pass

    def after_request(self, response):
        try:
            ms = int((time.time() - self._start) * 1000)
            method = _flask_request.method if _flask_request else '?'
            path = _flask_request.path if _flask_request else '/'
            if _req:
                _req.post(f"{self.endpoint}/api/log", json={
                    'apiName': self.api_name,
                    'category': self.category,
                    'method': method,
                    'path': path,
                    'status': response.status_code,
                    'responseTime': ms,
                    'status': 'error' if response.status_code >= 400 else 'healthy',
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
                }, timeout=2)
        except:
            pass
        return response

def create_logger(endpoint=None, api_name='API', category='General'):
    try:
        return _Logger(endpoint or LOG_ENDPOINT, api_name, category)
    except:
        # Return a dummy that does nothing
        class _Dummy:
            def before_request(self): pass
            def after_request(self, r): return r
        return _Dummy()
