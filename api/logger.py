# API Monitor Logger Client (Python/Flask) — Vercel-compatible
import time
import requests
from flask import request as flask_request

LOG_ENDPOINT = 'https://api-monitor-jeshankhalkho-ux.vercel.app'

class MonitorLogger:
    def __init__(self, endpoint, api_name, category='General'):
        self.endpoint = endpoint or LOG_ENDPOINT
        self.api_name = api_name
        self.category = category
        self._start = 0

    def _send(self, entry):
        try:
            requests.post(f"{self.endpoint}/api/log", json=entry, timeout=3)
        except:
            pass

    def before_request(self):
        self._start = time.time()

    def after_request(self, response):
        elapsed = int((time.time() - self._start) * 1000)
        try:
            method = flask_request.method
            path = flask_request.path
        except:
            method = '?'
            path = '/'
        self._send({
            'apiName': self.api_name,
            'category': self.category,
            'method': method,
            'path': path,
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


def create_logger(endpoint=None, api_name='API', category='General'):
    return MonitorLogger(endpoint, api_name, category)
