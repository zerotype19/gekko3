#!/bin/bash
# Helper script to run the Gekko3 Dashboard
# Usage: ./brain/run_dashboard.sh

cd "$(dirname "$0")/.." || exit
python3 -m streamlit run brain/dashboard.py
