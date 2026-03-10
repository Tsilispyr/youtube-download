"""
Equalizer blueprint: Standalone equalizer/visualizer demo.
Mounted at /AudioWeb/equalizer
Inspired by Webamp + Butterchurn — custom implementation with Web Audio API.
"""
from flask import Blueprint, render_template

equalizer_bp = Blueprint("equalizer", __name__, url_prefix="/AudioWeb/equalizer")


@equalizer_bp.route("/")
def index():
    return render_template("equalizer.html")
