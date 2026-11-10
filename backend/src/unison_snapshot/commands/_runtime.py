"""Shared CLI parsing and existing failure receipts."""
import argparse
from datetime import datetime, timezone
import json
from ..alpaca_market import AlpacaMarketError
from ..house import HouseIndexError
from ..disclosure_candidate import DisclosureCandidateError
from ..congress_members import CongressMemberError
from ..senate import SenateEfdError, SenateHttpError
from ..senate_members import SenateRosterError
from ..senate_identity import SenateIdentityError
from ..senate_history import SenateHistoryError
from ..oge import OgeCatalogError, OgeHttpError
from ..release_readiness import ReleaseReadinessError
from ._io import _write_atomic


def invoke(command, configure, execute):
    parser = argparse.ArgumentParser(prog="python -m unison_snapshot " + command)
    configure(parser)
    parser.set_defaults(command=command)
    args = parser.parse_args()
    try:
        execute(args)
    except (ValueError, RuntimeError, HouseIndexError, DisclosureCandidateError, AlpacaMarketError,
            SenateEfdError, SenateRosterError, SenateIdentityError, SenateHistoryError,
            CongressMemberError, OgeCatalogError, ReleaseReadinessError,
            KeyError, OSError,
            json.JSONDecodeError) as exc:
        from ..source_failures import record as record_source_failure
        record_source_failure(args.command,exc,filing_year=getattr(args,'year',None))
        if args.command == 'discover-oge' and args.failure_output:
            _write_atomic(args.failure_output, {'schema_version': 'oge-collection-failure/v1',
                'source_id': 'oge', 'http_status': exc.status if isinstance(exc, OgeHttpError) else None,
                'failed_step': 'collect_catalog', 'recorded_at': datetime.now(timezone.utc).isoformat()})
        if (args.command == "discover-senate-efd" and args.failure_output and
                isinstance(exc, SenateHttpError)):
            _write_atomic(args.failure_output, {
                "schema_version": "senate-collection-failure/v1",
                "source_id": "senate_efd", "status": "failed",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **exc.diagnostic,
            })
        parser.exit(2, f"Snapshot operation failed: {exc}\n")
