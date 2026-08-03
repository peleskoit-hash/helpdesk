from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.client_card_config_transfer import cli


if __name__ == "__main__":
    raise SystemExit(cli())
