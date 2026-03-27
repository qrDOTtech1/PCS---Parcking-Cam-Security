import sys
import os

project_home = "/home/Wlansolo"
if project_home not in sys.path:
    sys.path = [project_home] + sys.path

from app import app, socketio

application = app
