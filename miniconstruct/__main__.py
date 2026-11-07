from __future__ import annotations

import argparse

import uvicorn
from miniconstruct.h3.guide_acquisition import GuideAcquisitionError, require_guides


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MiniConstruct local server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", default=8743, type=int, help="Bind port (default: 8743)")
    parser.add_argument("--reload", action="store_true", help="Reload after source changes")
    parser.add_argument("--verbose-http", action="store_true", help="Log all HTTP requests, including successes")
    args = parser.parse_args()
    try:
        require_guides()
    except GuideAcquisitionError as exc:
        parser.exit(status=2, message=f"\n{exc}\n")
    uvicorn.run(
        "miniconstruct.app:app" if args.verbose_http else "miniconstruct.http_logging:quiet_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=not args.verbose_http,
        access_log=args.verbose_http,
    )


if __name__ == "__main__":
    main()
