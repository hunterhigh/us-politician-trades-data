"""One lazy public entry for the production command implementations."""
import importlib
import sys

COMMANDS = {'assemble-source': ('assemble_source', 'Save a qualified source and assemble current peer candidates'),
'senate-source-state': ('senate_source_state', 'Persist Senate gate, failure, collection and review state'),
'drain-house-ptr': ('drain_house_ptr', 'Archive indexed House PTRs within a recoverable time budget'),
'data-trees': ('data_trees', 'Prepare or publish fixed source data worktrees'),
'build-cached-candidate': ('build_cached_candidate',
                            'Publish current disclosures using existing or asynchronously prepared prices.'),
 'build-disclosure-status': ('build_disclosure_status',
                             'Build processing observations from frozen existing trees, without source '
                             'collection.'),
 'build-oge-manual-cover-dates': ('build_oge_manual_cover_dates',
                                  'Persist source-bound human cover-date readings without changing '
                                  'qualification.'),
 'build-whitehouse-278t-candidate': ('build_whitehouse_278t_candidate',
                                     'Build a local OGE review candidate from complete White House 278-T '
                                     'evidence.'),
 'build-whitehouse-annual-review': ('build_whitehouse_annual_review',
                                    'Materialize source-qualified and blocked White House annual holdings in '
                                    'review.'),
 'build-whitehouse-reported-index': ('build_whitehouse_reported_index',
                                     'Materialize filer-attributed White House review rows without '
                                     'publication gates.'),
 'demo': ('demo', 'One command: publish synthetic data, reconstruct pinned version, render baseline.'),
 'enrich-whitehouse-annual-tickers': ('enrich_whitehouse_annual_tickers',
                                      'Persist unique Alpaca-name ticker mappings into the White House '
                                      'review candidate.'),
 'observe-house-annual-sections': ('observe_house_annual_sections',
                                   'Write a House annual Schedule A/B sidecar from archived PDF and '
                                   'extraction.'),
 'observe-oge-278t-amount-column': ('observe_oge_278t_amount_column',
                                    'Probe notification and amount columns of saved 2024 OGE physical '
                                    'slots.'),
 'observe-oge-278t-date-column': ('observe_oge_278t_date_column',
                                  'Probe the date column of saved 2024 OGE 278-T physical slots.'),
 'observe-oge-278t-fields': ('observe_oge_278t_fields',
                             'Read saved slot observations into a conservative, read-only field ledger.'),
 'observe-oge-278t-printed-numbers': ('observe_oge_278t_printed_numbers',
                                      'Conserve printed row numbers over saved 2024 OGE grid observations.'),
 'observe-oge-278t-raster-slots': ('observe_oge_278t_raster_slots',
                                   'Observe OCR words inside confirmed slots of selected archived 278-T '
                                   'pages.'),
 'observe-oge-278t-type-column': ('observe_oge_278t_type_column',
                                  'Probe the Type column of saved 2024 OGE 278-T physical slots.'),
 'observe-oge-annual-scan-covers': ('observe_oge_annual_scan_covers',
                                    'Observe, but do not qualify, covers rejected by the strict annual '
                                    'parser.'),
 'observe-oge-annual-scan-dates': ('observe_oge_annual_scan_dates',
                                   'Replay six annual-catalog cover date blocks, including one amendment '
                                   'letter.'),
 'observe-oge-annual-scan-sections': ('observe_oge_annual_scan_sections',
                                      'Generate a read-only OGE scanned annual section ledger from an '
                                      'archived PDF.'),
 'oge-annual-review': ('oge_annual_review',
                       'Publish review-only extraction audits for archived OGE annual reports.'),
 'oge-whitehouse': ('oge_whitehouse',
                    'Build White House disclosure coverage from archived official OGE pages.'),
 'overlay-whitehouse-annual-candidate': ('overlay_whitehouse_annual_candidate',
                                         'Overlay the complete White House annual review subset on the OGE '
                                         'candidate.'),
 'prepare-oge-annual-runtime': ('prepare_oge_annual_runtime',
                                'Prepare bounded saved annual inputs offline from controlled '
                                'review/evidence.'),
 'prepare-oge-ptr-generic': ('prepare_oge_ptr_generic',
                             'Prepare one archived ordinary native PTR pair without collection or OCR.'),
 'prepare-senate-paper-runtime': ('prepare_senate_paper_runtime',
                                  'Prepare selected existing Senate replay bundles for the saved-input CLI.'),
 'publication-source-scope': ('publication_source_scope',
                              'Exclude collection/parser-only files from disposable publication worktrees.'),
 'run-house-annual-sections': ('run_house_annual_sections',
                               'Run bounded House annual sidecar lane after the normal holding parser.'),
 'senate-annual': ('senate_annual', "Collect and replay current senators' latest public eFD annual reports."),
 'source-assembly-status': ('source_assembly_status',
                            'Expose one source-workflow assembly attempt without implying publication.'),
 'verify-prepared-publication': ('verify_prepared_publication',
                                 'Read a prepared publication from remote immutable commits before moving '
                                 'production refs.'),
 'whitehouse-coverage': ('whitehouse_coverage',
                         'Summarize public White House report links without claiming canonical facts.'),
 'whitehouse-disclosures': ('whitehouse_disclosures',
                            'Bounded public White House disclosures ingestion; no Form 201 requests.'),
 'whitehouse-extract': ('whitehouse_extract',
                        'Extract archived White House public OGE forms into review-only evidence.')}


def register(subparsers):
    for name, (_, description) in COMMANDS.items():
        # Actual arguments and --help belong to the selected implementation.
        subparsers.add_parser(name, help=description, add_help=False)


def dispatch(arguments):
    if not arguments or arguments[0] not in COMMANDS:
        return False
    command, *options = arguments
    module = importlib.import_module('.commands.' + COMMANDS[command][0], 'unison_snapshot')
    previous = sys.argv
    try:
        sys.argv = ['python -m unison_snapshot ' + command, *options]
        result = module.main()
        if result not in (None, 0):
            raise SystemExit(result)
    finally:
        sys.argv = previous
    return True
