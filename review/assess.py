from evidence.codec import decode_artifact
from evidence.tolerance_codec import decode_tolerance_artifact
from reports.publish import publish_report, publish_tolerance_report
from review.ratios import compute_ratio_set
from review.request import bind_request
from review.tolerance_solver import minimum_residual_sum, normalize_intervals


def assess_and_publish(connection, artifact_text, request):
    bound = bind_request(decode_artifact(artifact_text), request)
    ratios = compute_ratio_set(bound)
    artifact = bound.artifact
    return publish_report(
        connection, artifact_id=artifact.artifact_id, revision_id=artifact.revision_id,
        revision_fingerprint=artifact.revision_fingerprint, inlet=bound.request.inlet,
        outlet=bound.request.outlet, ratios=ratios,
    )


def assess_tolerance_and_publish(connection, artifact_text, intervals):
    # A V1 envelope or any malformed body is rejected before touching the
    # archive, so invalid applications leave no partial results.
    artifact = decode_tolerance_artifact(artifact_text)
    evidence = artifact.evidence
    ordered = normalize_intervals(evidence, intervals)
    minimum = minimum_residual_sum(evidence, ordered)
    within_budget = minimum is not None and minimum <= evidence.budget
    return publish_tolerance_report(
        connection, artifact_id=artifact.artifact_id, revision_id=artifact.revision_id,
        revision_fingerprint=artifact.revision_fingerprint, limit=evidence.budget,
        intervals=ordered, minimum_residual_sum=minimum, within_budget=within_budget,
    )