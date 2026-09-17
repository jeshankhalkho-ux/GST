# API Monitor Logger Client (Python/Flask)
# Usage:
#   from logger import create_logger
#   log = create_logger('https://your-monitor.vercel.app', 'Vehicle API', 'Vehicle')
#   app = Flask(__name__)
#   app.before_request(log.before_request)
#   app.after_request(log.after_request)

import time
import threading
import requests

LOG_ENDPOINT = 'https://api-monitor-jeshankhalkho-ux.vercel.app'

class MonitorLogger:
    def __init__(self, endpoint, api_name, category='General'):
        self.endpoint = endpoint or LOG_ENDPOINT
        self.api_name = api_name
        self.category = category
        self._local = threading.local()

    def _send(self, entry):
        try:
            threading.Thread(
                target=lambda: requests.post(
                    f"{self.endpoint}/api/log",
                    json=entry,
                    timeout=5
                ),
                daemon=True
            ).start()
        except:
            pass

    def before_request(self):
        self._local.start = time.time()
        self._local.method = None
        self._local.path = None

    def after_request(self, response):
        elapsed = int((time.time() - getattr(self._local, 'start', time.time())) * 1000)
        from flask import request
        self._send({
            'apiName': self.api_name,
            'category': self.category,
            'method': request.method,
            'path': request.path,
            'status': response.status_code,
            'responseTime': elapsed,
            'status': 'error' if response.status_code >= 400 else 'healthy',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
        })
        return response

    def log(self, method='GET', path='/', status=200, response_time=0, **kwargs):
        self._send({
            'apiName': self.api_name,
            'category': self.category,
            'method': method,
            'path': path,
            'status': status,
            'responseTime': response_time,
            'status': 'error' if status >= 400 else 'healthy',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
            **kwargs,
        })

    def error(self, path='/', error=None, response_time=0):
        self._send({
            'apiName': self.api_name,
            'category': self.category,
            'method': 'ERROR',
            'path': path,
            'status': 500,
            'responseTime': response_time,
            'status': 'error',
            'errorMessage': str(error)[:200] if error else '',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
        })


def create_logger(endpoint=None, api_name='API', category='General'):
    return MonitorLogger(endpoint, api_name, category)
