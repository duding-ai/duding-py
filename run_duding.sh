#!/bin/bash
# Expand PATH so Automator can find all programs
export PATH="$HOME/Library/Python/3.9/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Go to the project folder (exit if not found)
cd "/Volumes/Extreme SSD/projects/duding-py" || exit

# Activate your virtual environment
source venv/bin/activate
export PYTHONUNBUFFERED=1

# Stop any old servers running on port 8000
if lsof -ti :8000 >/dev/null 2>&1; then
  echo "Clearing old server on port 8000..."
  kill -9 $(lsof -ti :8000) 2>/dev/null
fi

# Pull the latest code
git pull origin main

# Open VS Code safely
open -a "Visual Studio Code" .

# Start FastAPI server and open browser
uvicorn app:app --reload --reload-dir . --reload-dir templates --reload-dir static &
sleep 2
open "http://127.0.0.1:8000/setup"
wait

