import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from bench.store import BenchLedger
from calibration.fit import fit_frozen_revision
from evidence.issue import issue_calibration, issue_tolerance_calibration
from evidence.register import IssueRegister
from evidence.tolerance_codec import decode_tolerance_artifact, encode_tolerance_artifact
from reports.publish import initialize_archive
from reports.retrieve import ReportNotFound, retrieve_tolerance_report
from review.assess import assess_and_publish, assess_tolerance_and_publish
from review.request import RatioRequest
from review.tolerance_solver import minimum_residual_sum, normalize_intervals


SAMPLES = Path(__file__).resolve().parents[1] / 'samples'
TOLERANCE_CASES = json.loads((SAMPLES / 'tolerance.json').read_text(encoding='utf-8'))


def issue_tolerance_text(connection, case):
    ledger, register = BenchLedger(connection), IssueRegister(connection)
    ledger.initialize()
    register.initialize()
    ledger.accept_batch(**case['batch'])
    revision = ledger.freeze_current()
    artifact = issue_tolerance_calibration(fit_frozen_revision(revision, case['limit']), register)
    return revision, artifact, register.get(artifact.artifact_id)


def tolerance_counts(connection):
    reports = connection.execute('SELECT COUNT(*) FROM tolerance_reports').fetchone()[0]
    receipts = connection.execute('SELECT COUNT(*) FROM tolerance_receipts').fetchone()[0]
    return reports, receipts


class ToleranceSampleTests(unittest.TestCase):
    def test_every_sample_request_matches_frozen_expectation(self):
        for case in TOLERANCE_CASES:
            with self.subTest(case=case['name']), closing(sqlite3.connect(':memory:')) as connection:
                revision, artifact, text = issue_tolerance_text(connection, case)
                self.assertEqual(
                    (artifact.artifact_id, artifact.revision_id, artifact.revision_fingerprint),
                    (artifact.artifact_id, revision.revision_id, revision.fingerprint),
                )
                decoded = decode_tolerance_artifact(text)
                self.assertEqual(json.loads(text)['version'], 2)
                self.assertEqual(decoded.artifact_id, artifact.artifact_id)
                evidence = decoded.evidence
                for private in revision.cohort.columns:
                    self.assertNotIn(private, text)
                for request in case['requests']:
                    ordered = normalize_intervals(evidence, request['intervals'])
                    minimum = minimum_residual_sum(evidence, ordered)
                    expected = (
                        None if request['minimum_residual_sum'] is None
                        else Fraction(request['minimum_residual_sum'])
                    )
                    self.assertEqual(minimum, expected)
                    self.assertEqual(
                        minimum is not None and minimum <= evidence.budget,
                        request['within_budget'],
                    )

    def test_zero_margin_parts_pay_one_and_four(self):
        soft, stiff = TOLERANCE_CASES[0], TOLERANCE_CASES[1]
        with closing(sqlite3.connect(':memory:')) as soft_conn, closing(sqlite3.connect(':memory:')) as stiff_conn:
            _, _, soft_text = issue_tolerance_text(soft_conn, soft)
            _, _, stiff_text = issue_tolerance_text(stiff_conn, stiff)
            soft_ev = decode_tolerance_artifact(soft_text).evidence
            stiff_ev = decode_tolerance_artifact(stiff_text).evidence
            self.assertEqual(minimum_residual_sum(soft_ev, (('A', Fraction(1), Fraction(2)),)), Fraction(1))
            self.assertEqual(minimum_residual_sum(stiff_ev, (('A', Fraction(1), Fraction(2)),)), Fraction(4))


class ToleranceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        _, self.artifact, self.text = issue_tolerance_text(self.connection, TOLERANCE_CASES[2])
        self.evidence = decode_tolerance_artifact(self.text).evidence

    def test_evidence_is_exact_and_self_contained(self):
        self.assertEqual(self.evidence.catalog.names, ('A', 'B', 'C', 'D'))
        self.assertEqual(self.evidence.center, (0, 0, 0, 5))
        # C = A + B and D is constant at five.
        matrix = self.evidence.cost_shape
        for value in matrix[3]:
            self.assertEqual(value, 0)
        self.assertEqual(matrix[2][0], 1)
        self.assertEqual(matrix[2][1], 1)
        self.assertEqual(self.evidence.residual_floor, 0)
        self.assertEqual(self.evidence.budget, 2)
        roundtrip = encode_tolerance_artifact(decode_tolerance_artifact(self.text))
        self.assertEqual(roundtrip, self.text)

    def test_single_point_and_omitted_channels_and_reduced_rank(self):
        # Single-point consistent triple passes at exactly the budget.
        ordered = normalize_intervals(self.evidence, [['C', 2, 2], ['A', '1.0', 1], ['B', 1, 1]])
        self.assertEqual(ordered, (('A', Fraction(1), Fraction(1)),
                                   ('B', Fraction(1), Fraction(1)),
                                   ('C', Fraction(2), Fraction(2))))
        self.assertEqual(minimum_residual_sum(self.evidence, ordered), Fraction(2))
        # Contradictory single points are unreachable.
        conflict = normalize_intervals(self.evidence, [['A', 1, 1], ['B', 1, 1], ['C', 3, 3]])
        self.assertIsNone(minimum_residual_sum(self.evidence, conflict))
        # Constant channel honored through the reduced-rank relationship.
        self.assertEqual(
            minimum_residual_sum(self.evidence, normalize_intervals(self.evidence, [['D', 5, 5]])),
            0,
        )
        self.assertIsNone(
            minimum_residual_sum(self.evidence, normalize_intervals(self.evidence, [['D', 4, 4]]))
        )

    def test_endpoint_forms_and_closure(self):
        forms = [
            (1, 2, Fraction(1), Fraction(2)),
            ('1', '2', Fraction(1), Fraction(2)),
            ('1/2', '3/2', Fraction(1, 2), Fraction(3, 2)),
            (Fraction(1), Fraction(2), Fraction(1), Fraction(2)),
        ]
        for lower, upper, expected_low, expected_high in forms:
            with self.subTest(lower=lower, upper=upper):
                ordered = normalize_intervals(self.evidence, [['A', lower, upper]])
                self.assertEqual(ordered, (('A', expected_low, expected_high),))
        point = normalize_intervals(self.evidence, [['A', '3/2', '3/2']])
        self.assertEqual(point[0][1], point[0][2])


class ToleranceEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        _, _, self.text = issue_tolerance_text(self.connection, TOLERANCE_CASES[2])

    def _reject(self, envelope):
        with self.assertRaises(ValueError):
            decode_tolerance_artifact(envelope)

    def test_v1_and_v2_envelopes_do_not_mix(self):
        ledger, register = BenchLedger(self.connection), IssueRegister(self.connection)
        revision = ledger.freeze_current()
        v1 = issue_calibration(fit_frozen_revision(revision, 2), register)
        v1_text = register.get(v1.artifact_id)
        self._reject(v1_text)
        self._reject(self.text.replace('"version":2', '"version":1'))
        self._reject(self.text.replace('"version":2', '"version":3'))

    def test_duplicate_fields_and_malformed_bodies_rejected(self):
        self._reject('{}')
        self._reject(self.text.replace('"version":2', '"version":2,"version":2'))
        body = json.loads(self.text)
        self._reject(json.dumps({**body, 'artifact_id': '  '}))
        self._reject(json.dumps({**body, 'revision_fingerprint': 'z' * 64}))
        self._reject(json.dumps({**body, 'channels': ['A', 'A']}))
        self._reject(json.dumps({**body, 'residual_floor': '1/0'}))
        self._reject(json.dumps({**body, 'budget': '-1'}))
        self._reject(json.dumps({**body, 'residual_floor': '999999'}))
        shape = body['cost_shape']
        self._reject(json.dumps({**body, 'cost_shape': [row[:-1] for row in shape]}))
        negative = [row[:] for row in shape]
        negative[0][0] = '-1'
        self._reject(json.dumps({**body, 'cost_shape': negative}))
        asymmetric = [row[:] for row in shape]
        asymmetric[0][1] = '2'
        self._reject(json.dumps({**body, 'cost_shape': asymmetric}))


class ToleranceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.archive = sqlite3.connect(':memory:')
        self.addCleanup(self.archive.close)
        initialize_archive(self.archive)
        with closing(sqlite3.connect(':memory:')) as source:
            _, _, self.text = issue_tolerance_text(source, TOLERANCE_CASES[2])
            _, _, self.floor_text = issue_tolerance_text(source, TOLERANCE_CASES[3])

    def test_publish_retrieve_and_fixed_report_fields(self):
        report, receipt = assess_tolerance_and_publish(self.archive, self.text, [['C', 3, 3]])
        self.assertIsInstance(report.report_id, int)
        self.assertGreater(report.report_id, 0)
        self.assertEqual(report.limit, Fraction(2))
        self.assertEqual(report.minimum_residual_sum, Fraction(9, 2))
        self.assertFalse(report.within_budget)
        self.assertEqual(report.intervals, (('C', Fraction(3), Fraction(3)),))
        self.assertEqual(receipt.report_id, report.report_id)
        stored, stored_receipt = retrieve_tolerance_report(self.archive, report.report_id)
        self.assertEqual((stored, stored_receipt), (report, receipt))
        self.assertEqual(stored_receipt.receipt_id, f'receipt-{report.report_id}')

    def test_unreachable_request_still_publishes_none_and_fails_budget(self):
        report, receipt = assess_tolerance_and_publish(
            self.archive, self.text, [['A', 1, 1], ['B', 1, 1], ['C', 3, 3]]
        )
        self.assertIsNone(report.minimum_residual_sum)
        self.assertFalse(report.within_budget)
        stored, _ = retrieve_tolerance_report(self.archive, report.report_id)
        self.assertIsNone(stored.minimum_residual_sum)

    def test_unknown_report_and_unknown_artifact_ids(self):
        with self.assertRaises(ReportNotFound):
            retrieve_tolerance_report(self.archive, 4242)
        with self.assertRaises(ReportNotFound):
            retrieve_tolerance_report(self.archive, 0)
        with closing(sqlite3.connect(':memory:')) as source:
            ledger, register = BenchLedger(source), IssueRegister(source)
            ledger.initialize()
            register.initialize()
            with self.assertRaises(KeyError):
                register.get('unknown/artifact')

    def test_invalid_requests_raise_value_error_without_writes(self):
        invalid = [
            [], (), [['Z', 0, 1]], [['A', 2, 1]], [['A', True, 1]], [['A', 1.5, 2]],
            [['A', '1/0', 2]], [['A', 0, 1, 2]], 'x', (('A', 1, 2), ('A', 2, 3)),
            [['A', 1]], [['', 0, 1]], [[' A', 0, 1]], [['A', None, 1]], [42],
        ]
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    assess_tolerance_and_publish(self.archive, self.text, request)
        self.assertEqual(tolerance_counts(self.archive), (0, 0))

    def test_bad_envelope_never_publishes_and_v1_entry_rejects_v2(self):
        with self.assertRaises(ValueError):
            assess_tolerance_and_publish(self.archive, '{}', [['A', 0, 1]])
        with self.assertRaises(ValueError):
            assess_and_publish(self.archive, self.text, RatioRequest('A', 'A'))
        self.assertEqual(tolerance_counts(self.archive), (0, 0))

    def test_reopen_archive_keeps_fixed_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.sqlite3'
            with closing(sqlite3.connect(path)) as archive:
                initialize_archive(archive)
                report, receipt = assess_tolerance_and_publish(archive, self.floor_text, [['A', 1, 1]])
            with closing(sqlite3.connect(path)) as archive:
                initialize_archive(archive)
                initialize_archive(archive)
                stored, stored_receipt = retrieve_tolerance_report(archive, report.report_id)
            self.assertEqual(stored, report)
            self.assertEqual(stored_receipt, receipt)

    def test_publish_rollback_when_receipt_write_fails(self):
        self.archive.execute('''
            CREATE TEMP TRIGGER reject_tolerance_receipt BEFORE INSERT ON tolerance_receipts
            BEGIN SELECT RAISE(ABORT, 'tolerance receipt write interrupted'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'tolerance receipt write interrupted'):
            assess_tolerance_and_publish(self.archive, self.text, [['C', 3, 3]])
        self.assertFalse(self.archive.in_transaction)
        self.assertEqual(tolerance_counts(self.archive), (0, 0))
        self.archive.execute('DROP TRIGGER reject_tolerance_receipt')
        report, receipt = assess_tolerance_and_publish(self.archive, self.text, [['C', 3, 3]])
        self.assertEqual(tolerance_counts(self.archive), (1, 1))
        self.assertEqual(retrieve_tolerance_report(self.archive, report.report_id), (report, receipt))

    def test_commit_error_preserves_existing_snapshot_and_is_retryable(self):
        existing = assess_tolerance_and_publish(self.archive, self.floor_text, [['A', 1, 1]])
        self.archive.executescript('''
            CREATE TABLE tolerance_probe_parent (id INTEGER PRIMARY KEY);
            CREATE TABLE tolerance_probe_child (
                id INTEGER REFERENCES tolerance_probe_parent(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER interrupt_tolerance_commit AFTER INSERT ON tolerance_receipts
            BEGIN INSERT INTO tolerance_probe_child VALUES (1); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            assess_tolerance_and_publish(self.archive, self.text, [['C', 3, 3]])
        self.assertFalse(self.archive.in_transaction)
        self.assertEqual(tolerance_counts(self.archive), (1, 1))
        self.assertEqual(retrieve_tolerance_report(self.archive, existing[0].report_id), existing)
        self.archive.execute('DROP TRIGGER interrupt_tolerance_commit')
        retried = assess_tolerance_and_publish(self.archive, self.text, [['C', 3, 3]])
        self.assertEqual(tolerance_counts(self.archive), (2, 2))
        self.assertEqual(retrieve_tolerance_report(self.archive, retried[0].report_id), retried)


class ToleranceCorrectionTests(unittest.TestCase):
    def test_correction_issues_new_revision_but_keeps_original_bytes(self):
        case = TOLERANCE_CASES[3]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bench.sqlite3'
            archive = Path(directory) / 'archive.sqlite3'
            with closing(sqlite3.connect(source)) as connection:
                ledger, register = BenchLedger(connection), IssueRegister(connection)
                ledger.initialize()
                register.initialize()
                ledger.accept_batch(**case['batch'])
                first_revision = ledger.freeze_current()
                first = issue_tolerance_calibration(
                    fit_frozen_revision(first_revision, case['limit']), register
                )
                first_text = register.get(first.artifact_id)
            with closing(sqlite3.connect(archive)) as archive_conn:
                initialize_archive(archive_conn)
                first_report, first_receipt = assess_tolerance_and_publish(
                    archive_conn, first_text, case['requests'][0]['intervals']
                )
                self.assertEqual(first_report.minimum_residual_sum, 4)
            with closing(sqlite3.connect(source)) as connection:
                ledger, register = BenchLedger(connection), IssueRegister(connection)
                ledger.initialize()
                register.initialize()
                ledger.correct_readings(case['correction'])
                corrected_revision = ledger.freeze_current()
                corrected = issue_tolerance_calibration(
                    fit_frozen_revision(corrected_revision, case['limit']), register
                )
                corrected_text = register.get(corrected.artifact_id)
                self.assertNotEqual(corrected_revision.revision_id, first_revision.revision_id)
                self.assertEqual(register.get(first.artifact_id), first_text)
                self.assertEqual(
                    ledger.load_revision(first_revision.revision_id).fingerprint,
                    first_revision.fingerprint,
                )
            with closing(sqlite3.connect(archive)) as archive_conn:
                initialize_archive(archive_conn)
                corrected_report, _ = assess_tolerance_and_publish(
                    archive_conn, corrected_text, case['requests'][0]['intervals']
                )
                self.assertEqual(corrected_report.minimum_residual_sum, 0)
                self.assertTrue(corrected_report.within_budget)
                reopened, reopened_receipt = retrieve_tolerance_report(
                    archive_conn, first_report.report_id
                )
            self.assertEqual(reopened.minimum_residual_sum, 4)
            self.assertEqual(reopened.artifact_id, first.artifact_id)
            self.assertEqual(reopened_receipt.receipt_id, first_receipt.receipt_id)


if __name__ == '__main__':
    unittest.main()
