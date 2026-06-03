"""Entry point for the Horizen tstZEN staking web app.

    python app.py        # dev server on http://127.0.0.1:5000
"""

from horizen_staking import create_app
from horizen_staking.config import config

app = create_app(config)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=config.debug)
