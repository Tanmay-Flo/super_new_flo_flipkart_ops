"""
service_account_to_secrets.py
-----------------------------
Prints the [gcp_service_account] block for secrets.toml from a Google
service-account JSON key file, with the private key correctly escaped.

Usage (from the project folder):
    python service_account_to_secrets.py logical-etching-422305-k8-6f0329c5456f.json

Copy the printed block into:
  * .streamlit/secrets.toml          (local)
  * Streamlit Cloud -> App settings -> Secrets   (deployed app)
"""

import json
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python service_account_to_secrets.py <service-account.json>")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as fh:
        info = json.load(fh)

    print("[gcp_service_account]")
    for key, value in info.items():
        # json.dumps gives a valid TOML basic string (escapes \n and quotes)
        print(f"{key} = {json.dumps(str(value))}")


if __name__ == "__main__":
    main()
