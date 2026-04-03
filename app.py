#!/usr/bin/env python3
"""
Docker Package Updater - Web Interface
=======================================
A Flask web app to scan, update, and rollback packages across Docker projects.
"""

import logging
from flask import Flask

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

from routes import register_routes
register_routes(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
