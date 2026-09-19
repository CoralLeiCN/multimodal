"""Verify uploaded R2 images and record their permanent URLs in PostgreSQL."""

import argparse
import json
from pathlib import Path

from app.core.config import ROOT, Settings
from app.core.db import make_engine, migrate
from app.core.locking import ingestion_lock
from app.services.r2_catalogue import apply_r2_urls, plan_r2_urls, r2_client
from botocore.exceptions import BotoCoreError, ClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Additional database dotenv file")
    parser.add_argument(
        "--apply", action="store_true", help="Migrate and commit verified URLs"
    )
    parser.add_argument(
        "--report", type=Path, default=ROOT / "data/processed/r2_catalogue_urls.json"
    )
    args = parser.parse_args()
    settings = (
        Settings(_env_file=(ROOT / ".env", ROOT / ".env.local", args.env_file))
        if args.env_file
        else Settings()
    )
    engine = make_engine(settings)
    client = r2_client(settings)
    try:
        with ingestion_lock(settings):
            if args.apply:
                migrate(settings)
            plan = plan_r2_urls(engine, settings, client)
            # Preserve previous values before the transaction for audit/rollback.
            report = {"status": "verified", "image_count": len(plan), "images": plan}
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
            updated = apply_r2_urls(engine, plan) if args.apply else 0
            report.update(
                status="applied" if args.apply else "verified", updated=updated
            )
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "images"}))
    except ClientError as error:
        parser.exit(1, f"R2 verification failed: {error.response['Error']['Code']}\n")
    except BotoCoreError as error:
        parser.exit(1, f"R2 verification failed: {type(error).__name__}\n")
    except ValueError as error:
        parser.exit(1, f"R2 catalogue update failed: {error}\n")
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
