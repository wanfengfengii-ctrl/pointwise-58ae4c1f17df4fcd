import json
from dataclasses import dataclass
from fractions import Fraction

from evidence.channels import ChannelCatalog, _check_shape
from evidence.codec import _unique_fields


@dataclass(frozen=True)
class ToleranceEvidence:
    """Self-contained public reading relationship for a V2 calibration.

    ``center`` is the public reading of the least-squares calibration,
    ``cost_shape`` is the rational matrix M = W G^-1 W' relating public
    reading offsets to residual cost, and ``residual_floor`` is the least
    achievable total residual sum of squares.
    """

    catalog: ChannelCatalog
    center: tuple[Fraction, ...]
    cost_shape: tuple[tuple[Fraction, ...], ...]
    residual_floor: Fraction
    budget: Fraction

    def __post_init__(self):
        center = tuple(self.center)
        shape = tuple(tuple(row) for row in self.cost_shape)
        size = len(self.catalog.names)
        if len(center) != size or len(shape) != size or any(len(row) != size for row in shape):
            raise ValueError('tolerance evidence dimensions do not match the channel catalog')
        if any(type(value) not in (int, Fraction) for row in (center, *shape) for value in row):
            raise ValueError('tolerance evidence requires exact rational values')
        if type(self.residual_floor) not in (int, Fraction) or type(self.budget) not in (int, Fraction):
            raise ValueError('tolerance evidence requires exact rational cost values')
        center = tuple(map(Fraction, center))
        shape = tuple(tuple(map(Fraction, row)) for row in shape)
        floor, budget = Fraction(self.residual_floor), Fraction(self.budget)
        _check_shape(shape)
        if floor < 0:
            raise ValueError('residual floor must be nonnegative')
        if budget < floor:
            raise ValueError('tolerance budget must cover the residual floor')
        object.__setattr__(self, 'center', center)
        object.__setattr__(self, 'cost_shape', shape)
        object.__setattr__(self, 'residual_floor', floor)
        object.__setattr__(self, 'budget', budget)


@dataclass(frozen=True)
class ToleranceArtifact:
    artifact_id: str
    revision_id: str
    revision_fingerprint: str
    evidence: ToleranceEvidence


def encode_tolerance_artifact(artifact):
    evidence = artifact.evidence
    return json.dumps({
        'version': 2,
        'artifact_id': artifact.artifact_id,
        'revision_id': artifact.revision_id,
        'revision_fingerprint': artifact.revision_fingerprint,
        'channels': evidence.catalog.names,
        'center': [str(value) for value in evidence.center],
        'cost_shape': [[str(value) for value in row] for row in evidence.cost_shape],
        'residual_floor': str(evidence.residual_floor),
        'budget': str(evidence.budget),
    }, sort_keys=True, separators=(',', ':'))


def _exact_row(raw):
    if type(raw) is not list or not raw or any(type(value) is not str for value in raw):
        raise ValueError('tolerance values must be nonempty arrays of rational strings')
    try:
        return tuple(Fraction(value) for value in raw)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError('invalid rational in tolerance evidence') from error


def _exact_scalar(raw, field):
    if type(raw) is not str:
        raise ValueError(f'tolerance {field} must be a rational string')
    try:
        return Fraction(raw)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f'invalid tolerance {field}') from error


def decode_tolerance_artifact(text):
    if type(text) is not str:
        raise ValueError('calibration envelope must be text')
    body = json.loads(text, object_pairs_hook=_unique_fields)
    fields = {
        'version', 'artifact_id', 'revision_id', 'revision_fingerprint', 'channels',
        'center', 'cost_shape', 'residual_floor', 'budget',
    }
    if type(body) is not dict or body.keys() != fields:
        raise ValueError('invalid tolerance envelope fields')
    if type(body['version']) is not int or body['version'] != 2:
        raise ValueError('unsupported tolerance envelope version')
    for key in ('artifact_id', 'revision_id'):
        if type(body[key]) is not str or not body[key].strip():
            raise ValueError(f'invalid {key}')
    fingerprint = body['revision_fingerprint']
    if type(fingerprint) is not str or len(fingerprint) != 64 or any(
        char not in '0123456789abcdef' for char in fingerprint
    ):
        raise ValueError('invalid revision fingerprint')
    if type(body['channels']) is not list or type(body['cost_shape']) is not list:
        raise ValueError('invalid tolerance catalog or cost shape')
    center = _exact_row(body['center'])
    shape = tuple(_exact_row(row) for row in body['cost_shape'])
    if len(shape) != len(center) or any(len(row) != len(center) for row in shape):
        raise ValueError('tolerance cost shape dimensions do not match the catalog')
    evidence = ToleranceEvidence(
        ChannelCatalog(tuple(body['channels'])), center, shape,
        _exact_scalar(body['residual_floor'], 'residual floor'),
        _exact_scalar(body['budget'], 'budget'),
    )
    return ToleranceArtifact(body['artifact_id'], body['revision_id'], fingerprint, evidence)
