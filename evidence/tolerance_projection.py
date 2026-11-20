from fractions import Fraction

from calibration.rational_linalg import multiply_matrices, solve_linear_system
from evidence.channels import ChannelCatalog
from evidence.tolerance_codec import ToleranceEvidence


def project_tolerance_evidence(fitted, wiring):
    """Preserve the full joint reachability of the public channels offline.

    For a frozen revision with design matrix X and response y, beta0 is the
    least-squares center, G = X'X the gram matrix and r_min = ||X beta0 - y||^2
    the residual floor. A public channel reads t = o + W beta. Writing
    q = t - (o + W beta0), the exact minimum residual sum over all real
    parameters reaching a feasible reading offset q is

        r_min + q' (W G^-1 W')^+ q,

    where the plus denotes the Moore-Penrose inverse and feasibility is
    membership in the column space of M = W G^-1 W'. The matrix M, the public
    center, the floor and the original budget are all exact rationals and
    fully determine every channel relationship, including constant and
    linearly dependent channels.
    """

    catalog = ChannelCatalog(tuple(channel.name for channel in wiring))
    least_squares = fitted.least_squares
    coefficients = tuple(channel.coefficients for channel in wiring)
    fitted_column = tuple((value,) for value in least_squares.center)
    linear_centers = multiply_matrices(coefficients, fitted_column)
    center = tuple(
        linear[0] + channel.offset
        for linear, channel in zip(linear_centers, wiring, strict=True)
    )
    inverse_actions = tuple(
        solve_linear_system(least_squares.gram, row) for row in coefficients
    )
    cost_shape = multiply_matrices(coefficients, tuple(zip(*inverse_actions, strict=True)))
    return ToleranceEvidence(
        catalog, center, cost_shape,
        Fraction(least_squares.residual_sum), Fraction(fitted.budget.limit),
    )
