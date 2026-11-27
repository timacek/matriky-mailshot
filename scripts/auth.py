"""Jednorázové OAuth přihlášení ke Gmailu (Desktop client z Google Cloud).

Použití:
  1. V Google Cloud Console zapni Gmail API a vytvoř OAuth client typu "Desktop app".
  2. Stažený JSON ulož jako credentials.json do kořene projektu (nebo jinam a nastav
     "credentials_path" v config.json).
  3. Spusť: python scripts/auth.py
     Prohlížeč otevře souhlas; token se uloží do ~/.config/matriky-mailshot/token.json
     (na Windows %APPDATA%\\matriky-mailshot\\token.json). Případný jiný domov nastav
     proměnnou MAILSHOT_CONFIG_HOME.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Authorize Gmail access.")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--credentials", help="override the credentials.json path")
    args = parser.parse_args(argv)

    from google_auth_oauthlib.flow import InstalledAppFlow

    cfg = common.load_config(args.config)
    credentials_path = Path(args.credentials or cfg["credentials_path"])
    if not credentials_path.is_file():
        print(f"AUTH missing credentials file: {credentials_path}", file=sys.stderr)
        print(
            "Stáhni OAuth client (Desktop app) z Google Cloud a ulož ho tam, "
            "nebo zadej --credentials.",
            file=sys.stderr,
        )
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), common.SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")

    target = common.token_path(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(json.loads(creds.to_json()), indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(target, 0o600)
    print(f"AUTH ok token={target} scopes={','.join(common.SCOPES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
