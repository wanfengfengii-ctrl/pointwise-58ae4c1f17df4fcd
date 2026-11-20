from fractions import Fraction

from calibration.rational_linalg import SingularMatrixError, solve_linear_system


# Each enumerated channel is either inactive, pinned to its lower endpoint or
# pinned to its upper endpoint. Constant channels (a zero row in the cost
# shape) cannot move and are handled up front.
_INACTIVE, _LOWER, _UPPER = range(3)


def _bound(value):
    if type(value) is bool or type(value) not in (int, str, Fraction):
        raise ValueError('tolerance endpoints must be exact rational values')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f'invalid tolerance endpoint {value!r}') from error


def normalize_intervals(evidence, intervals):
    """Validate a nonempty named closed-interval request against the artifact.

    Returns channel-ordered ``(name, low, high)`` triples with exact rational
    endpoints. Structure, endpoint, ordering, duplicate and unknown channel
    problems raise ``ValueError``.
    """
    if type(intervals) not in (list, tuple) or not intervals:
        raise ValueError('tolerance request must be a nonempty list or tuple')
    names = evidence.catalog.names
    bounds = {}
    for entry in intervals:
        if type(entry) not in (list, tuple) or len(entry) != 3:
            raise ValueError('each tolerance interval must be [name, lower, upper]')
        name, lower, upper = entry
        if type(name) is not str or not name or name.strip() != name:
            raise ValueError('tolerance channel name must be a nonempty clean string')
        if name not in names:
            raise ValueError(f'unknown tolerance channel {name!r}')
        if name in bounds:
            raise ValueError(f'duplicate tolerance channel {name!r}')
        low, high = _bound(lower), _bound(upper)
        if low > high:
            raise ValueError('tolerance interval lower endpoint exceeds upper endpoint')
        bounds[name] = (low, high)
    return tuple((name, *bounds[name]) for name in sorted(bounds))


def minimum_residual_sum(evidence, intervals):
    """Exact minimum total residual sum satisfying every closed interval.

    Returns a ``Fraction`` or ``None`` when the joint request is unreachable.
    Only the self-contained V2 evidence is used: public center ``t0``, cost
    shape ``M`` and residual floor ``r_min``. Reading offsets q = t - t0 live
    in range(M); for feasible q the minimum cost is r_min + q' M^+ q. The
    convex box-constrained program is solved by enumerating independent
    active endpoint sets and checking the exact rational KKT conditions.

    For an active set A the stationarity conditions give q = M mu with mu
    supported on A, and the principal block satisfies M_{A,A} mu_A = b_A with
    b_A the requested endpoints. Lower-active entries need mu >= 0,
    upper-active entries need mu <= 0, and inactive coordinates must land in
    their boxes. The minimum cost is r_min + mu_A' b_A.

    Raw interval requests are normalized first; already normalized triples
    (the output of :func:`normalize_intervals`) are accepted unchanged.
    """
    ordered = intervals
    if not (type(intervals) is tuple and intervals
            and all(type(entry) is tuple and len(entry) == 3 and type(entry[0]) is str
                    and type(entry[1]) is Fraction and type(entry[2]) is Fraction
                    for entry in intervals)):
        ordered = normalize_intervals(evidence, intervals)
    names = evidence.catalog.names
    matrix = evidence.cost_shape
    center = evidence.center

    constrained = []
    for name, low, high in ordered:
        index = names.index(name)
        if not any(matrix[index]):
            # Constant channels are pinned by the frozen wiring.
            if not low <= center[index] <= high:
                return None
            continue
        constrained.append((index, low - center[index], high - center[index]))
    if not constrained:
        return Fraction(evidence.residual_floor)

    size = len(constrained)
    best = None

    def candidate(active):
        active_rows = [(position, state) for position, state in enumerate(active)
                       if state != _INACTIVE]
        if not active_rows:
            if all(low <= 0 <= high for _, low, high in constrained):
                return Fraction(0)
            return None
        indices = [constrained[position][0] for position, _ in active_rows]
        hessian = tuple(tuple(matrix[row][column] for column in indices) for row in indices)
        target = tuple(
            constrained[position][1 if state == _LOWER else 2]
            for position, state in active_rows
        )
        try:
            multipliers = solve_linear_system(hessian, target)
        except SingularMatrixError:
            return None
        for (_, state), multiplier in zip(active_rows, multipliers, strict=True):
            if state == _LOWER and multiplier < 0:
                return None
            if state == _UPPER and multiplier > 0:
                return None
        offset = {position: constrained[position][1 if state == _LOWER else 2]
                  for position, state in active_rows}
        for position, (_, low, high) in enumerate(constrained):
            if position not in offset:
                offset[position] = sum(
                    matrix[constrained[position][0]][constrained[active_position][0]] * multiplier
                    for (active_position, _), multiplier in zip(active_rows, multipliers, strict=True)
                )
            if not low <= offset[position] <= high:
                return None
        # q = M mu lies in range(M) by construction and q_A = b_A, so the
        # quadratic q' M^+ q reduces to the pairing mu_A' b_A.
        cost = sum(
            multipliers[slot] * offset[position]
            for slot, (position, _) in enumerate(active_rows)
        )
        return cost if cost >= 0 else None

    def enumerate_states(position, active):
        nonlocal best
        if position == size:
            value = candidate(active)
            if value is not None and (best is None or value < best):
                best = value
            return
        for state in (_INACTIVE, _LOWER, _UPPER):
            active.append(state)
            enumerate_states(position + 1, active)
            active.pop()

    enumerate_states(0, [])
    if best is None:
        return None
    return Fraction(evidence.residual_floor) + best
