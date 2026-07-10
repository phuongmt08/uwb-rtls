# Auto-activate virtual environment when entering software directory
if [ -d ".venv" ]; then
    source .venv/Scripts/activate
    echo "Activated virtual environment: .venv"
fi
