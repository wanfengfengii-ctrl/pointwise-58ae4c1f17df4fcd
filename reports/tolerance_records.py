import json
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class ToleranceReport:
    report_id: int
    artifact_id: str
    revision_id: str
    revision_fingerprint: str
    limit: Fraction
    intervals: tuple[tuple[str, Fraction, Fraction], ...]
    minimum_residual_sum: Fraction | None
    within_budget: bool


def _encode_fraction(value):
    if value is None:
        return None
    return str(Fraction(value))


def encode_tolerance_report(report):
    return json.dumps({
        'limit': str(report.limit),
        'intervals': [[name, str(low), str(high)] for name, low, high in report.intervals],
        'minimum_residual_sum': _encode_fraction(report.minimum_residual_sum),
        'within_budget': report.within_budget,
    }, sort_keys=True, separators=(',', ':'))


def _decode_fraction(value, field):
    if type(value) is not str:
        raise ValueError(f'invalid archived tolerance {field}')
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f'invalid archived tolerance {field}') from error
    if str(result) != value:
        raise ValueError(f'noncanonical archived tolerance {field}')
    return result


def decode_tolerance_payload(text):
    payload = json.loads(text)
    if type(payload) is not dict or payload.keys() != {'limit', 'intervals', 'minimum_residual_sum', 'within_budget'}:
        raise ValueError('invalid archived tolerance payload')
    if type(payload['within_budget']) is not bool:
        raise ValueError('invalid archived tolerance verdict')
    raw_intervals = payload['intervals']
    if type(raw_intervals) is not list or not raw_intervals:
        raise ValueError('invalid archived tolerance intervals')
    intervals = []
    for entry in raw_intervals:
        if type(entry) is not list or len(entry) != 3 or any(type(part) is not str for part in entry):
            raise ValueError('invalid archived tolerance interval')
        name, low, high = entry
        if not name:
            raise ValueError('invalid archived tolerance channel')
        intervals.append((name, _decode_fraction(low, 'endpoint'), _decode_fraction(high, 'endpoint')))
    ordered = tuple(sorted(intervals))
    if ordered != tuple(intervals):
        raise ValueError('archived tolerance intervals are not canonical')
    if any(ordered[index][0] == ordered[index - 1][0] for index in range(1, len(ordered))):
        raise ValueError('archived tolerance intervals contain duplicates')
    minimum = payload['minimum_residual_sum']
    if minimum is not None:
        minimum = _decode_fraction(minimum, 'minimum')
    return {
        'limit': _decode_fraction(payload['limit'], 'limit'),
        'intervals': ordered,
        'minimum_residual_sum': minimum,
        'within_budget': payload['within_budget'],
    }
