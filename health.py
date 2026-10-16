"""Local package health only; Test connection checks the provider key."""

import json
import ssl

import jev

if __name__ == "__main__":
    ssl.create_default_context()
    print(
        json.dumps(
            {
                "ok": True,
                "status": "healthy",
                "extension_id": "flowsteward.typesafe-ai",
                "endpoint": jev.HOST,
                "provider_verified": False,
            }
        )
    )
