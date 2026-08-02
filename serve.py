#!/usr/bin/env python3
"""Run the SOTA WFS server (port 8080, matching the existing ngrok tunnel)."""

import os

from waitress import serve

from sota_wfs.app import create_app

if __name__ == "__main__":
    port = int(os.environ.get("SOTA_WFS_PORT", "8080"))
    print(f"sota-wfs listening on 0.0.0.0:{port}")
    serve(
        create_app(),
        host="0.0.0.0",
        port=port,
        threads=8,
        # ngrok proxies from localhost; trust its X-Forwarded-* headers so
        # capabilities hrefs are built with the public https URL.
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers={
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
            "x-forwarded-port",
        },
    )
