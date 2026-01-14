# Running the Gekko3 Dashboard

## Quick Start

From the project root (`/Users/kevinmcgovern/gekko3`):

```bash
python3 -m streamlit run brain/dashboard.py
```

Or use the helper script:

```bash
./brain/run_dashboard.sh
```

## If you're in the brain directory:

```bash
cd /Users/kevinmcgovern/gekko3
python3 -m streamlit run brain/dashboard.py
```

## Note

The `streamlit` command may not be in your PATH. Always use:
- `python3 -m streamlit run brain/dashboard.py` (from project root)
- Or `python3 -m streamlit run dashboard.py` (if you're in the brain directory)

The dashboard will open at: http://localhost:8501
